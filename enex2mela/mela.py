"""Writes .melarecipe JSON files and bundles them into a .melarecipes archive."""

from __future__ import annotations

import base64
import json
import uuid
import zipfile
from pathlib import Path

from .images import to_jpeg
from .recipe import Recipe

NAMESPACE = uuid.UUID("6f1c2a3e-6d0b-4a51-9c39-1b0e7c1f5a10")


def stable_id(*parts: str) -> str:
    """Same note -> same id across reruns, so re-importing replaces rather than duplicates."""
    return str(uuid.uuid5(NAMESPACE, "\x1f".join(parts))).upper()


def to_mela(recipe: Recipe, recipe_id: str, categories: list[str], images: list[bytes], max_images: int = 2) -> dict:
    encoded = []
    for data in images:
        if len(encoded) >= max_images:
            break
        jpeg = to_jpeg(data, max_edge=1600)
        if jpeg:
            encoded.append(base64.b64encode(jpeg).decode())
    return {
        "id": recipe_id,
        "title": recipe.title,
        "text": recipe.description,
        "ingredients": "\n".join(recipe.ingredients),
        "instructions": "\n".join(recipe.instructions),
        "notes": recipe.notes,
        "yield": recipe.yield_,
        "prepTime": recipe.prep_time,
        "cookTime": recipe.cook_time,
        "totalTime": recipe.total_time,
        "nutrition": recipe.nutrition,
        "link": recipe.link,
        "categories": categories,
        "images": encoded,
        "favorite": False,
        "wantToCook": False,
    }


def write_recipe(data: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{data['id']}.melarecipe"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return path


def bundle(files: list[Path], archive: Path) -> Path:
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)
    return archive
