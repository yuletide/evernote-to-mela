"""Rule-based recipe parser for typed text, pasted text, and OCR output.

Two strategies:
1. Labelled: find "Ingredients" and "Directions"-style headings and split on them.
2. Unlabelled: many cookbook pages and recipe cards have no headings. Find the longest run of
   lines that look like ingredients (start with a quantity or contain a unit), and treat the
   prose after it as the method.
"""

from __future__ import annotations

import re

from .recipe import Recipe

INGREDIENT_HEAD = re.compile(r"^(ingredients?|you(?:'ll)? need|what you need)\b[\s:]*$", re.I)
INSTRUCTION_HEAD = re.compile(
    r"^(directions?|instructions?|method|preparation|prep|steps|how to make( it)?|to make)\b[\s:]*$", re.I
)
NOTES_HEAD = re.compile(r"^(notes?|tips?|variations?|cook'?s notes?)\b[\s:]*$", re.I)
YIELD = re.compile(r"^(?:serves|servings|yield|yields|makes)\s*:?\s*(.+)$", re.I)
PREP = re.compile(r"^prep(?:aration)?\s*time\s*:?\s*(.+)$", re.I)
COOK = re.compile(r"^cook(?:ing)?\s*time\s*:?\s*(.+)$", re.I)
TOTAL = re.compile(r"^total\s*time\s*:?\s*(.+)$", re.I)
STEP_NUMBER = re.compile(r"^(?:step\s*)?\d+[.)]\s*", re.I)
BULLET = "•·-–*▪◦"

FRACTIONS = "½⅓⅔¼¾⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞"
QUANTITY = re.compile(
    rf"^[{re.escape(BULLET)}\s]*(\d|[{FRACTIONS}]|(a|an|one|two|three|four|five|six|half|a few|several|pinch|dash|handful)\b)",
    re.I,
)
UNITS = (
    r"cups?|c\.|tbsps?|tbs|tbl|tablespoons?|tsps?|teaspoons?|t\.|oz|ounces?|lbs?|pounds?|g|grams?|kg|kilos?|"
    r"ml|millilit(?:er|re)s?|l|lit(?:er|re)s?|pints?|quarts?|qt|gallons?|cloves?|cans?|tins?|sticks?|pinch(?:es)?|"
    r"dash(?:es)?|slices?|bunch(?:es)?|sprigs?|heads?|packages?|pkgs?|stalks?|knobs?|handfuls?"
)
UNIT = re.compile(rf"\b\d[\d/.\s{FRACTIONS}-]*\s*({UNITS})\b", re.I)
TO_TASTE = re.compile(r"\b(to taste|for (serving|garnish|garnishing|frying|greasing|dusting|drizzling|topping|decorating)|as needed|optional)\b", re.I)
IMPERATIVE = re.compile(
    r"^(add|bake|beat|blend|boil|bring|chill|chop|combine|cook|cool|cover|cut|drain|fold|fry|garnish|grease|"
    r"heat|knead|let|line|mix|place|pour|preheat|reduce|remove|roast|roll|season|serve|simmer|slice|spread|"
    r"sprinkle|stir|strain|toss|transfer|whisk|in a)\b",
    re.I,
)


COUNTED = re.compile(rf"\b(\d+|[{FRACTIONS}])\s+[a-z]", re.I)  # "juice of 2 lemons"


def looks_like_ingredient(line: str) -> bool:
    if len(line) > 90 or IMPERATIVE.match(line.lstrip(BULLET + " ")):
        return False
    if any(p.match(line) for p in (YIELD, PREP, COOK, TOTAL)):
        return False
    if QUANTITY.match(line) or UNIT.search(line) or TO_TASTE.search(line):
        return True
    return len(line) <= 45 and not line.endswith(".") and bool(COUNTED.search(line))


def _subheading(line: str) -> str | None:
    """'For the sauce:' -> 'For the sauce'. Short lines ending in a colon are group headings."""
    if line.endswith(":") and len(line) <= 60 and not re.search(r"\d", line):
        return line[:-1].strip()
    return None


def _section_lines(lines: list[str], strip_numbers: bool) -> list[str]:
    out = []
    for line in lines:
        head = _subheading(line)
        if head:
            out.append(f"# {head}")
            continue
        if strip_numbers:
            line = STEP_NUMBER.sub("", line)
        line = line.lstrip(BULLET + " ").strip()
        if line:
            out.append(line)
    return out


def _join_wrapped(lines: list[str]) -> list[str]:
    """OCR and narrow cards wrap sentences across lines. Rejoin lines that don't end a sentence."""
    out: list[str] = []
    for line in lines:
        starts_step = bool(STEP_NUMBER.match(line)) or _subheading(line) or line[:1] in BULLET
        if out and not starts_step and not re.search(r"[.!?:)]$", out[-1]) and line[:1].islower():
            out[-1] = f"{out[-1]} {line}"
        else:
            out.append(line)
    return out


def _metadata(recipe: Recipe, lines: list[str], title: str) -> list[str]:
    """Pull yield/times out of the preamble. Returns the remaining description lines."""
    rest = []
    for line in lines:
        for pattern, attr in ((YIELD, "yield_"), (PREP, "prep_time"), (COOK, "cook_time"), (TOTAL, "total_time")):
            m = pattern.match(line)
            if m:
                setattr(recipe, attr, m.group(1).strip())
                break
        else:
            if line.strip().lower() != title.strip().lower():
                rest.append(line)
    return rest


def _ingredient_run(lines: list[str]) -> tuple[int, int] | None:
    """Longest stretch of ingredient-looking lines (group headings allowed inside). (start, end) or None."""
    best = None
    i = 0
    while i < len(lines):
        if not looks_like_ingredient(lines[i]):
            i += 1
            continue
        start, hits, j = i, 0, i
        while j < len(lines):
            if looks_like_ingredient(lines[j]):
                hits += 1
            elif not (_subheading(lines[j]) or (len(lines[j]) < 40 and j + 1 < len(lines)
                                                 and looks_like_ingredient(lines[j + 1]))):
                break
            j += 1
        if hits >= 3 and (best is None or hits > best[2]):
            best = (start, j, hits)
        i = j
    return (best[0], best[1]) if best else None


def _split_notes(lines: list[str]) -> tuple[list[str], list[str]]:
    at = next((i for i, l in enumerate(lines) if NOTES_HEAD.match(l)), None)
    if at is None:
        return lines, []
    return lines[:at], lines[at + 1:]


def parse(title: str, lines: list[str]) -> Recipe:
    """Returns a Recipe; check `is_complete` to see whether it found both sections.

    `recipe.method_detail` says which strategy worked ("labelled" or "unlabelled").
    """
    recipe = Recipe(title=title)
    ing_at = next((i for i, l in enumerate(lines) if INGREDIENT_HEAD.match(l)), None)
    ins_at = next((i for i, l in enumerate(lines) if INSTRUCTION_HEAD.match(l)), None)

    if ing_at is not None and ins_at is not None and ins_at > ing_at:
        method, notes = _split_notes(lines[ins_at + 1:])
        recipe.description = "\n".join(_metadata(recipe, lines[:ing_at], title))
        recipe.ingredients = _section_lines(lines[ing_at + 1:ins_at], strip_numbers=False)
        recipe.instructions = _section_lines(_join_wrapped(method), strip_numbers=True)
        recipe.notes = "\n".join(notes)
        recipe.method_detail = "labelled"
        return recipe

    # Unlabelled (or only one heading): find the ingredient block, method is what follows.
    offset = ing_at + 1 if ing_at is not None else 0
    body = lines[offset:]
    run = _ingredient_run(body)
    if run is None:
        return recipe
    start, end = run
    after = lines[ins_at + 1:] if ins_at is not None and ins_at >= offset + end else body[end:]
    method, notes = _split_notes(after)
    recipe.description = "\n".join(_metadata(recipe, lines[:offset] + body[:start], title))
    recipe.ingredients = _section_lines(body[start:end], strip_numbers=False)
    recipe.instructions = _section_lines(_join_wrapped(method), strip_numbers=True)
    recipe.notes = "\n".join(notes)
    recipe.method_detail = "unlabelled"
    return recipe
