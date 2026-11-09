"""Triage a note into a bucket and run the right extractor on it."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import heuristic, jsonld, ocr
from .enex import Note
from .llm import ExtractionError, LLMExtractor, NotARecipe
from .recipe import Recipe

SITE_SUFFIX = re.compile(r"\s+[|–—-]\s+[^|–—]{2,40}$")
RECIPE_PREFIX = re.compile(r"^recipe\s*[:\-–]\s*", re.I)


def clean_title(title: str) -> str:
    """'Recipe: Best Chili | Example Site' -> 'Best Chili'."""
    return SITE_SUFFIX.sub("", RECIPE_PREFIX.sub("", title.strip())).strip() or title.strip()


TOO_SHORT_WORDS = 15  # unparseable notes shorter than this are lists or scraps, not recipes
ANNOTATION_MAX_WORDS = 120  # a web-clip note this short is a bookmark plus the owner's own comments


def bucket(note: Note) -> str:
    """web: clipped from a URL. image: mostly photos/scans/PDFs. text: typed or pasted. empty: nothing to convert."""
    words = len(note.text().split())
    has_media = bool(note.images or note.pdfs)
    if note.source_url and note.source_url.startswith(("http://", "https://")):
        return "web"
    if has_media and words < 60:
        return "image"
    if words:
        return "text"
    return "empty"


@dataclass
class Result:
    note: Note
    bucket: str
    method: str = ""
    status: str = "ok"  # ok | partial | skipped
    recipe: Recipe | None = None
    images: list[bytes] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Options:
    fetch_urls: bool = True
    ocr: bool = True
    llm: LLMExtractor | None = None  # None = never call the API


def _fallback(note: Note, result: Result, lines: list[str], reason: str) -> Result:
    """Nothing could split the note: keep all its text (including OCR text) in Mela's notes field."""
    result.recipe = Recipe(title=note.title, notes="\n".join(lines))
    result.method = "unparsed"
    result.status = "partial"
    result.warnings.append(reason)
    return result


def _llm(note: Note, result: Result, opts: Options, lines: list[str]) -> Result:
    try:
        recipe, warnings = opts.llm.extract(note)
    except NotARecipe:
        result.status = "skipped"
        result.method = "llm"
        result.warnings.append("model says this is not a recipe")
        return result
    except ExtractionError as e:
        return _fallback(note, result, lines, f"LLM: {e}")
    result.recipe = recipe
    result.method = "llm"
    result.warnings += warnings
    if not recipe.is_complete:
        result.status = "partial"
        result.warnings.append("missing ingredients or instructions")
    return result


def _ocr_lines(note: Note) -> list[str]:
    lines: list[str] = []
    for r in note.media_order():
        if r.is_image:
            lines += ocr.image_lines(r.data)
        elif r.is_pdf:
            lines += ocr.pdf_lines(r.data)
    return lines


def _local(note: Note, result: Result, opts: Options) -> Result:
    lines = note.lines()
    used_ocr = False
    if result.bucket == "image":
        if opts.ocr and ocr.backend():
            lines = lines + _ocr_lines(note)
            used_ocr = True
        elif opts.ocr:
            pdf_text = [l for r in note.pdfs for l in ocr.pdf_lines(r.data)]
            lines = lines + pdf_text

    parsed = heuristic.parse(note.title, lines)
    if parsed.is_complete:
        result.recipe = parsed
        result.method = "ocr" if used_ocr else "heuristic"
        if used_ocr:
            result.warnings.append("read by OCR; check quantities against the photo")
        if parsed.method_detail == "unlabelled":
            result.warnings.append("no section headings; ingredients/steps split by guesswork")
        return result

    if opts.llm:
        return _llm(note, result, opts, lines)

    if result.bucket != "image" and len(" ".join(lines).split()) < TOO_SHORT_WORDS:
        result.status = "skipped"
        result.method = "unparsed"
        result.warnings.append("too short to be a recipe")
        return result

    if result.bucket == "image" and not used_ocr:
        reason = "photo/scan: no OCR engine installed (see README) and LLM not enabled"
    elif used_ocr and not lines:
        reason = "OCR found no text (handwriting?); try --llm"
    else:
        reason = "couldn't split into ingredients and steps; fix in Mela or try --llm"
    return _fallback(note, result, lines, reason)


def convert(note: Note, opts: Options) -> Result:
    result = Result(note=note, bucket=bucket(note))
    if note.has_encrypted:
        result.warnings.append("note has encrypted text that couldn't be read")
    result.images = [r.data for r in note.media_order() if r.is_image]

    if result.bucket == "empty":
        result.status = "skipped"
        result.warnings.append("empty note")
        return result

    if result.bucket == "web" and opts.fetch_urls:
        recipe = jsonld.fetch(note.source_url)
        if recipe and recipe.is_complete:
            result.recipe = recipe
            result.method = "web"
            text = note.text()
            if text and len(text.split()) <= ANNOTATION_MAX_WORDS:
                recipe.notes = text
            if recipe.image_urls:
                img = jsonld.fetch_image(recipe.image_urls[0])
                if img:
                    result.images.insert(0, img)
        else:
            result.warnings.append("source page gone or has no recipe data; used the clipped text")

    if result.recipe is None:
        _local(note, result, opts)

    if result.recipe:
        if not result.recipe.title or result.recipe.title == note.title:
            result.recipe.title = clean_title(note.title)
        if not result.recipe.link and note.source_url:
            result.recipe.link = note.source_url
    return result


def load_category_map(path: str | Path | None) -> dict[str, str | None]:
    if not path:
        return {}
    raw = json.loads(Path(path).read_text())
    return {k.strip().lower(): v for k, v in raw.items()}


def categories(note: Note, mapping: dict[str, str | None], include_notebook: bool) -> list[str]:
    names = list(note.tags)
    if include_notebook:
        names.append(note.notebook)
    out: list[str] = []
    for name in names:
        key = name.strip().lower()
        mapped = mapping.get(key, name.strip())
        if mapped and mapped not in out:
            out.append(mapped)
    return out


def dedupe_key(recipe: Recipe) -> tuple[str, str]:
    title = re.sub(r"[^a-z0-9]+", " ", recipe.title.lower()).strip()
    return title, recipe.link.rstrip("/").lower()
