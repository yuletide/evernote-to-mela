"""Local-only paths: parsing without headings, OCR, column order."""
import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from enex2mela import heuristic, ocr
from enex2mela.convert import Options, convert
from enex2mela.enex import Note, Resource
from enex2mela.ocr import order_boxes

UNLABELLED = """Lemon Bars
Makes 16 bars
1 cup butter, softened
½ cup sugar
2 cups flour
For the filling:
4 eggs
1 ½ cups sugar
Juice of 2 lemons
Powdered sugar for dusting
Preheat oven to 350. Line a 9x13 pan with
parchment and press in the crust.
Bake 20 minutes.
Whisk the filling, pour over the hot crust and
bake 25 minutes more.""".splitlines()


def test_heuristic_without_headings():
    r = heuristic.parse("Lemon Bars", UNLABELLED)
    assert r.method_detail == "unlabelled"
    assert r.yield_ == "16 bars"
    assert r.ingredients[0] == "1 cup butter, softened"
    assert "# For the filling" in r.ingredients
    assert r.ingredients[-1] == "Powdered sugar for dusting"
    assert r.instructions == [
        "Preheat oven to 350. Line a 9x13 pan with parchment and press in the crust.",
        "Bake 20 minutes.",
        "Whisk the filling, pour over the hot crust and bake 25 minutes more.",
    ]


def test_heuristic_gives_up_on_prose():
    r = heuristic.parse("Idea", ["Try the chili place on 5th", "Ask Sam for the recipe"])
    assert not r.is_complete


def _card(lines: list[str]) -> bytes:
    img = Image.new("RGB", (1400, 120 + 110 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=48)
    for i, line in enumerate(lines):
        draw.text((80, 60 + i * 110), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.skipif(ocr.backend() is None, reason="no OCR engine installed")
def test_ocr_reads_a_rendered_card():
    data = _card(["Ingredients", "2 cups flour", "1 tsp salt", "3 eggs", "Method",
                  "Mix everything together.", "Bake for 30 minutes."])
    note = Note(title="Card", notebook="t", html='<en-note><en-media hash="x"/></en-note>',
                resources=[Resource(hash="x", mime="image/png", data=data)])
    r = convert(note, Options(fetch_urls=False))
    assert r.method == "ocr", r.warnings
    assert r.recipe.ingredients == ["2 cups flour", "1 tsp salt", "3 eggs"]
    assert r.recipe.instructions == ["Mix everything together.", "Bake for 30 minutes."]
    assert r.images == [data]


def test_two_column_reading_order():
    boxes = [("R1", 0.55, 0.1, 0.3, 0.05), ("L1", 0.05, 0.1, 0.3, 0.05),
             ("R2", 0.55, 0.2, 0.3, 0.05), ("L2", 0.05, 0.2, 0.3, 0.05),
             ("R3", 0.55, 0.3, 0.3, 0.05), ("L3", 0.05, 0.3, 0.3, 0.05),
             ("Title", 0.2, 0.02, 0.6, 0.05)]
    assert order_boxes(boxes) == ["Title", "L1", "L2", "L3", "R1", "R2", "R3"]
