import json
import zipfile
from pathlib import Path

from enex2mela import heuristic, jsonld
from enex2mela.cli import main
from enex2mela.convert import Options, bucket, convert
from enex2mela.enex import iter_notes

SAMPLE = Path(__file__).parent / "fixtures" / "sample.enex"


def notes():
    return {n.title: n for n in iter_notes(SAMPLE)}


def test_parse_enex():
    ns = notes()
    assert set(ns) == {"Banana Bread", "Aunt May's card", "Best Chili Ever | Example Site", "Shopping", "Blank"}
    bb = ns["Banana Bread"]
    assert bb.tags == ["Baking", "desserts"]
    assert bb.notebook == "sample"
    assert bb.created.year == 2019
    card = ns["Aunt May's card"]
    assert len(card.images) == 1 and card.media_order() == card.images


def test_buckets():
    ns = notes()
    assert bucket(ns["Banana Bread"]) == "text"
    assert bucket(ns["Aunt May's card"]) == "image"
    assert bucket(ns["Best Chili Ever | Example Site"]) == "web"
    assert bucket(ns["Blank"]) == "empty"


def test_heuristic_sections():
    r = heuristic.parse("Banana Bread", notes()["Banana Bread"].lines())
    assert r.is_complete
    assert r.ingredients == ["3 ripe bananas", "1/3 cup melted butter", "# For the topping", "2 tbsp brown sugar"]
    assert r.instructions == ["Heat oven to 350F.", "Mash bananas, stir in butter."]
    assert r.yield_ == "8" and r.prep_time == "15 min"
    assert r.notes == "Used walnuts last time, better."


def test_iso_duration():
    assert jsonld.iso_duration("PT1H30M") == "1 hr 30 min"
    assert jsonld.iso_duration("PT45M") == "45 min"
    assert jsonld.iso_duration("P0DT2H") == "2 hr"
    assert jsonld.iso_duration("about an hour") == "about an hour"


def test_jsonld_graph_with_sections():
    page = """<html><script type="application/ld+json">{"@context":"https://schema.org","@graph":[
      {"@type":"WebPage"},
      {"@type":["Recipe"],"name":"Chili &amp; Beans","recipeYield":["4","4 servings"],"cookTime":"PT2H",
       "recipeIngredient":["1 lb beef","2 cans beans"],
       "recipeInstructions":[{"@type":"HowToSection","name":"Cook","itemListElement":[
          {"@type":"HowToStep","text":"Brown the beef."},{"@type":"HowToStep","text":"Add beans."}]}],
       "image":{"@type":"ImageObject","url":"https://x/img.jpg"},
       "nutrition":{"@type":"NutritionInformation","calories":"420 kcal"}}]}</script></html>"""
    r = jsonld.recipe_from_html(page, "https://x/chili")
    assert r.title == "Chili & Beans"
    assert r.yield_ == "4 servings"
    assert r.cook_time == "2 hr"
    assert r.instructions == ["# Cook", "Brown the beef.", "Add beans."]
    assert r.image_urls == ["https://x/img.jpg"]
    assert r.nutrition == "calories: 420 kcal"


def test_web_clip_falls_back_to_clipped_text():
    r = convert(notes()["Best Chili Ever | Example Site"], Options(fetch_urls=False))
    assert r.method == "heuristic"
    assert r.recipe.link == "https://example.invalid/chili"


def test_end_to_end_without_llm(tmp_path):
    cats = tmp_path / "cats.json"
    cats.write_text(json.dumps({"desserts": "Dessert", "sample": None}))
    assert main([str(SAMPLE), "--out", str(tmp_path / "out"), "--no-fetch", "--no-ocr",
                 "--categories", str(cats), "--workers", "1"]) == 0
    archive = tmp_path / "out" / "import.melarecipes"
    with zipfile.ZipFile(archive) as zf:
        recipes = [json.loads(zf.read(n)) for n in zf.namelist()]
    by_title = {r["title"]: r for r in recipes}
    bb = by_title["Banana Bread"]
    assert bb["categories"] == ["Baking", "Dessert"]
    assert bb["ingredients"].splitlines()[2] == "# For the topping"
    card = by_title["Aunt May's card"]
    assert len(card["images"]) == 1  # kept so you can fix it by hand in Mela
    report = (tmp_path / "out" / "report.csv").read_text()
    assert ",image,unparsed,partial," in report
    assert "empty note" in report


def test_clean_title():
    from enex2mela.convert import clean_title
    assert clean_title("Recipe: Best Chili Ever | Example Site") == "Best Chili Ever"
    assert clean_title("Pad Thai - Serious Eats") == "Pad Thai"
    assert clean_title("Mom's Mac-and-Cheese") == "Mom's Mac-and-Cheese"


class _FakeMessages:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create(self, **kwargs):
        from types import SimpleNamespace
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=2000, output_tokens=500),
                               content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


def _fake_llm(tmp_path, monkeypatch, payload):
    from types import SimpleNamespace
    from enex2mela.llm import LLMExtractor
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    llm = LLMExtractor(cache_dir=tmp_path / "cache")
    fake = _FakeMessages(payload)
    llm.client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    return llm, fake


CARD = {"is_recipe": True, "title": "Aunt May's Scones", "description": "", "ingredients": ["2 cups flour", "1 egg"],
        "instructions": ["Mix.", "Bake 12 min."], "notes": "", "yield": "8", "prep_time": "", "cook_time": "12 min",
        "total_time": "", "nutrition": ""}


def test_image_note_goes_to_llm_with_image_and_caches(tmp_path, monkeypatch):
    llm, fake = _fake_llm(tmp_path, monkeypatch, CARD)
    note = notes()["Aunt May's card"]
    r = convert(note, Options(fetch_urls=False, ocr=False, llm=llm))
    assert r.method == "llm" and r.status == "ok"
    assert r.recipe.title == "Aunt May's Scones" and r.recipe.cook_time == "12 min"
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert [b["type"] for b in call["messages"][0]["content"]] == ["image", "text"]
    convert(note, Options(fetch_urls=False, ocr=False, llm=llm))
    assert len(fake.calls) == 1  # second run served from cache
    assert llm.calls == 1 and llm.cached == 1
    assert "about $0.02" in llm.summary()


def test_llm_call_cap_falls_back_without_calling(tmp_path, monkeypatch):
    llm, fake = _fake_llm(tmp_path, monkeypatch, CARD)
    llm.max_calls = 0
    r = convert(notes()["Aunt May's card"], Options(fetch_urls=False, ocr=False, llm=llm))
    assert fake.calls == [] and r.status == "partial"
    assert "llm-max-calls" in r.warnings[-1]


def test_llm_not_a_recipe_is_skipped(tmp_path, monkeypatch):
    llm, _ = _fake_llm(tmp_path, monkeypatch, {**CARD, "is_recipe": False})
    r = convert(notes()["Shopping"], Options(fetch_urls=False, ocr=False, llm=llm))
    assert r.status == "skipped"
