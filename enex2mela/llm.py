"""Claude-based extraction for notes the heuristic parser can't handle: messy text, photos, scans."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from pathlib import Path

import anthropic

from .enex import Note
from .images import to_jpeg
from .recipe import Recipe

DEFAULT_MODEL = "claude-opus-5-5"
PROMPT_VERSION = "1"
MAX_IMAGES = 8
MAX_TEXT_CHARS = 60_000

# USD per million tokens (input, output), for the cost readout only.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

SYSTEM = """You convert recipe notes from a personal Evernote archive into structured recipe fields for the Mela recipe app.

The note may be typed text, a clipped web page, photos of cookbook pages, or handwritten recipe cards. Read all of it, including the images.

Rules:
- Copy wording and quantities exactly as written. Do not convert units, fix spelling, rephrase, or invent anything. If something is illegible, write [illegible].
- ingredients: one ingredient per item. When the recipe groups ingredients ("For the dough", "Sauce"), add the group name as its own item prefixed with "# ".
- instructions: one step per item, without step numbers. Group headings use the same "# " prefix.
- If a recipe spans several images (e.g. two cookbook pages), combine them into one recipe.
- If there are several distinct recipes, extract the main one and mention the others in notes.
- notes: personal comments, substitutions, ratings, or anything else in the note that isn't part of the recipe itself. Keep the owner's annotations; they matter.
- Clipped web pages contain navigation, ads, and comments. Ignore those.
- Leave a field as an empty string or empty list when the note doesn't say. Never guess times or servings.
- is_recipe is false for things that aren't recipes: shopping lists, meal plans, restaurant recommendations, blank notes."""

SCHEMA = {
    "type": "object",
    "properties": {
        "is_recipe": {"type": "boolean"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "ingredients": {"type": "array", "items": {"type": "string"}},
        "instructions": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
        "yield": {"type": "string"},
        "prep_time": {"type": "string"},
        "cook_time": {"type": "string"},
        "total_time": {"type": "string"},
        "nutrition": {"type": "string"},
    },
    "required": [
        "is_recipe", "title", "description", "ingredients", "instructions", "notes",
        "yield", "prep_time", "cook_time", "total_time", "nutrition",
    ],
    "additionalProperties": False,
}


class ExtractionError(Exception):
    pass


class NotARecipe(Exception):
    pass


class LLMExtractor:
    def __init__(self, model: str = DEFAULT_MODEL, cache_dir: str | Path = ".cache/llm", effort: str = "low",
                 max_calls: int | None = None):
        self.client = anthropic.Anthropic(max_retries=6)
        self.model = model
        self.effort = effort
        self.max_calls = max_calls
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.calls = 0
        self.cached = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def _reserve_call(self) -> None:
        with self._lock:
            if self.max_calls is not None and self.calls >= self.max_calls:
                raise ExtractionError(f"skipped: --llm-max-calls {self.max_calls} reached")
            self.calls += 1

    def summary(self) -> str:
        line = (f"Claude: {self.calls} API calls, {self.cached} from cache, "
                f"{self.input_tokens:,} input / {self.output_tokens:,} output tokens")
        if self.model in PRICES:
            pin, pout = PRICES[self.model]
            cost = self.input_tokens / 1e6 * pin + self.output_tokens / 1e6 * pout
            line += f", about ${cost:.2f}"
        return line

    def _content(self, note: Note) -> tuple[list[dict], list[str]]:
        warnings = []
        blocks: list[dict] = []
        media = [r for r in note.media_order() if r.is_image or r.is_pdf]
        if len([r for r in media if r.is_image]) > MAX_IMAGES:
            warnings.append(f"only first {MAX_IMAGES} images sent to the model")
        sent_images = 0
        for r in media:
            if r.is_pdf:
                blocks.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf",
                               "data": base64.standard_b64encode(r.data).decode()},
                })
                continue
            if sent_images >= MAX_IMAGES:
                continue
            jpeg = to_jpeg(r.data, max_edge=1568, quality=90)
            if jpeg is None:
                warnings.append(f"could not read image {r.filename or r.mime}")
                continue
            sent_images += 1
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg",
                           "data": base64.standard_b64encode(jpeg).decode()},
            })
        text = note.text()
        if len(text) > MAX_TEXT_CHARS:
            raise ExtractionError(f"note text is {len(text)} characters; too long to send without splitting")
        header = f"Note title: {note.title}\n"
        if note.source_url:
            header += f"Clipped from: {note.source_url}\n"
        if note.tags:
            header += f"Tags: {', '.join(note.tags)}\n"
        blocks.append({"type": "text", "text": f"{header}\nNote text:\n{text or '(no text; see images)'}"})
        return blocks, warnings

    def _cache_key(self, note: Note) -> str:
        h = hashlib.sha256()
        for part in (self.model, self.effort, PROMPT_VERSION, SYSTEM, note.title, note.source_url or "", note.html):
            h.update(part.encode())
        for r in note.resources:
            h.update(r.hash.encode())
        return h.hexdigest()

    def _call(self, content: list[dict]) -> dict:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
        with self._lock:
            self.input_tokens += response.usage.input_tokens or 0
            self.output_tokens += response.usage.output_tokens or 0
        if response.stop_reason == "refusal":
            raise ExtractionError("model declined this note")
        if response.stop_reason == "max_tokens":
            raise ExtractionError("model output was cut off")
        text = "".join(b.text for b in response.content if b.type == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ExtractionError(f"model returned invalid JSON: {e}") from e

    def extract(self, note: Note) -> tuple[Recipe, list[str]]:
        """Returns (recipe, warnings). Raises NotARecipe or ExtractionError."""
        key = self._cache_key(note)
        cache_file = self.cache_dir / f"{key}.json"
        content, warnings = self._content(note)
        if cache_file.exists():
            data = json.loads(cache_file.read_text())
            with self._lock:
                self.cached += 1
        else:
            self._reserve_call()
            try:
                data = self._call(content)
            except anthropic.APIStatusError as e:
                raise ExtractionError(f"API error {e.status_code}: {e.message}") from e
            except anthropic.APIConnectionError as e:
                raise ExtractionError(f"API connection error: {e}") from e
            cache_file.write_text(json.dumps(data, indent=2))
        if not data.get("is_recipe"):
            raise NotARecipe()
        recipe = Recipe(
            title=data["title"] or note.title,
            description=data["description"],
            ingredients=[i.strip() for i in data["ingredients"] if i.strip()],
            instructions=[i.strip() for i in data["instructions"] if i.strip()],
            notes=data["notes"],
            yield_=data["yield"],
            prep_time=data["prep_time"],
            cook_time=data["cook_time"],
            total_time=data["total_time"],
            nutrition=data["nutrition"],
        )
        return recipe, warnings
