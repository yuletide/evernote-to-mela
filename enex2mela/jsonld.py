"""Re-fetch a web clip's source page and read its schema.org Recipe data."""

from __future__ import annotations

import html
import json
import re

import requests
from bs4 import BeautifulSoup

from .recipe import Recipe

USER_AGENT = "Mozilla/5.0 (compatible; enex2mela recipe importer)"
DURATION = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+(?:\.\d+)?)H)?(?:(?P<m>\d+(?:\.\d+)?)M)?(?:(?P<s>\d+)S)?)?$", re.I
)


def iso_duration(value) -> str:
    """'PT1H30M' -> '1 hr 30 min'. Passes through anything it doesn't recognise."""
    if not value or not isinstance(value, str):
        return ""
    m = DURATION.match(value.strip())
    if not m or not any(m.groupdict().values()):
        return value.strip()
    days = int(m["d"] or 0)
    hours = float(m["h"] or 0) + days * 24
    minutes = float(m["m"] or 0) + int(m["s"] or 0) / 60
    total = int(round(hours * 60 + minutes))
    if total == 0:
        return ""
    h, mins = divmod(total, 60)
    parts = []
    if h:
        parts.append(f"{h} hr")
    if mins:
        parts.append(f"{mins} min")
    return " ".join(parts)


def _clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = value[0] if value else ""
    text = BeautifulSoup(html.unescape(str(value)), "html.parser").get_text(" ")
    return " ".join(text.split())


def _is_recipe(node) -> bool:
    t = node.get("@type") if isinstance(node, dict) else None
    return t == "Recipe" or (isinstance(t, list) and "Recipe" in t)


def find_recipe_node(data):
    if isinstance(data, list):
        for item in data:
            found = find_recipe_node(item)
            if found:
                return found
    elif isinstance(data, dict):
        if _is_recipe(data):
            return data
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in data:
                found = find_recipe_node(data[key])
                if found:
                    return found
    return None


def _instructions(value) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        text = BeautifulSoup(value, "html.parser").get_text("\n")
        return [" ".join(l.split()) for l in text.splitlines() if l.strip()]
    if isinstance(value, dict):
        value = [value]
    for item in value or []:
        if isinstance(item, str):
            out += _instructions(item)
        elif isinstance(item, dict):
            if item.get("@type") == "HowToSection":
                if item.get("name"):
                    out.append(f"# {_clean(item['name'])}")
                out += _instructions(item.get("itemListElement", []))
            else:
                text = _clean(item.get("text") or item.get("name"))
                if text:
                    out.append(text)
    return out


def _images(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [value["url"]] if value.get("url") else []
    out = []
    for item in value or []:
        out += _images(item)
    return out


def _nutrition(value) -> str:
    if not isinstance(value, dict):
        return ""
    parts = []
    for key, val in value.items():
        if key.startswith("@") or not val:
            continue
        label = re.sub(r"(?<!^)(?=[A-Z])", " ", key).lower().replace(" content", "")
        parts.append(f"{label}: {_clean(val)}")
    return "\n".join(parts)


def recipe_from_node(node: dict, url: str) -> Recipe:
    yield_ = node.get("recipeYield")
    if isinstance(yield_, list):
        yield_ = next((y for y in yield_ if isinstance(y, str) and not y.isdigit()), yield_[0] if yield_ else "")
    return Recipe(
        title=_clean(node.get("name")),
        description=_clean(node.get("description")),
        ingredients=[_clean(i) for i in node.get("recipeIngredient") or node.get("ingredients") or [] if _clean(i)],
        instructions=_instructions(node.get("recipeInstructions")),
        yield_=_clean(yield_),
        prep_time=iso_duration(node.get("prepTime")),
        cook_time=iso_duration(node.get("cookTime")),
        total_time=iso_duration(node.get("totalTime")),
        nutrition=_nutrition(node.get("nutrition")),
        link=url,
        image_urls=_images(node.get("image")),
    )


def recipe_from_html(page: str, url: str) -> Recipe | None:
    soup = BeautifulSoup(page, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except json.JSONDecodeError:
            continue
        node = find_recipe_node(data)
        if node:
            return recipe_from_node(node, url)
    return None


def fetch(url: str, timeout: float = 15) -> Recipe | None:
    """Return the page's Recipe, or None if the page is gone or has no recipe data."""
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    except requests.RequestException:
        return None
    if resp.status_code != 200 or "html" not in resp.headers.get("content-type", "html"):
        return None
    return recipe_from_html(resp.text, url)


def fetch_image(url: str, timeout: float = 15) -> bytes | None:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    except requests.RequestException:
        return None
    if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
        return resp.content
    return None
