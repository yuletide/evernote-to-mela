"""Rule-based parser for typed or pasted recipes with recognisable section headings."""

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
        line = line.lstrip("•·-–* ").strip()
        if line:
            out.append(line)
    return out


def parse(title: str, lines: list[str]) -> Recipe:
    """Split lines into sections. Returns a Recipe; check `is_complete` to see if it worked."""
    recipe = Recipe(title=title)
    ing_at = next((i for i, l in enumerate(lines) if INGREDIENT_HEAD.match(l)), None)
    ins_at = next((i for i, l in enumerate(lines) if INSTRUCTION_HEAD.match(l)), None)
    if ing_at is None or ins_at is None or ins_at < ing_at:
        return recipe
    notes_at = next(
        (i for i, l in enumerate(lines) if i > ins_at and NOTES_HEAD.match(l)), len(lines)
    )

    preamble = []
    for line in lines[:ing_at]:
        for pattern, attr in ((YIELD, "yield_"), (PREP, "prep_time"), (COOK, "cook_time"), (TOTAL, "total_time")):
            m = pattern.match(line)
            if m:
                setattr(recipe, attr, m.group(1).strip())
                break
        else:
            if line.strip().lower() != title.strip().lower():
                preamble.append(line)

    recipe.description = "\n".join(preamble)
    recipe.ingredients = _section_lines(lines[ing_at + 1:ins_at], strip_numbers=False)
    recipe.instructions = _section_lines(lines[ins_at + 1:notes_at], strip_numbers=True)
    recipe.notes = "\n".join(lines[notes_at + 1:])
    return recipe
