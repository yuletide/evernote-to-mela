# evernote-to-mela

Converts Evernote recipe notes into a `.melarecipes` file that Mela imports in one go. See [PLAN.md](PLAN.md) for the reasoning behind the approach.

Everything runs locally by default. Claude is an opt-in last resort (`--llm`) for the notes nothing local could parse, and only those notes are sent.

Each note is sorted into one of four buckets:

| Bucket | What it is | Local method | With `--llm` |
|---|---|---|---|
| `web` | Web Clipper notes with a source URL | Re-fetches the page and reads its schema.org recipe data. Falls back to the clipped text if the page is gone. | Only if the clipped text won't parse |
| `text` | Typed or pasted recipes | Splits on "Ingredients"/"Directions" headings, or finds the ingredient list by its quantities and units when there are no headings | Only if that fails |
| `image` | Photos, scans, PDFs, handwritten cards | OCR on your machine, then the same text parser | Only if OCR text won't parse (e.g. messy handwriting) |
| `empty` | Nothing in it | Skipped | Skipped |

Anything that can't be parsed is still written to Mela with all its text (including OCR text) in the notes field and the photo attached, so nothing is lost. It's marked `partial` in the report. Tiny unparseable notes like shopping lists are skipped.

## Setup

```sh
pip install -r requirements.txt
```

OCR engine, for photos and scans:

- **Mac (recommended):** `pip install ocrmac`. Uses Apple's Vision framework, the same engine as Live Text in Photos. Good on printed pages, decent on neat handwriting, free and offline.
- **Linux/Windows:** install Tesseract (`brew install tesseract`, `apt install tesseract-ocr`, or the Windows installer), then `pip install pytesseract`. Fine on printed text, poor on handwriting.

Optional: `pip install pillow-heif` if your photos are HEIC.

Only for `--llm`: `export ANTHROPIC_API_KEY=sk-ant-...`

## 1. Export from Evernote

In the Evernote desktop app, right-click each recipe notebook → **Export notebook** → ENEX, with tags included. Put the files in `input/`. The filename becomes the notebook name.

For large accounts, [`evernote-backup`](https://github.com/vzhd1701/evernote-backup) exports everything at once:

```sh
pip install evernote-backup
evernote-backup init-db && evernote-backup sync && evernote-backup export input/
```

## 2. Triage

```sh
python -m enex2mela input/*.enex --triage-only
```

Prints how many notes fall into each bucket and which OCR engine it found, and writes `out/report.csv`. Converts nothing.

## 3. Local run

```sh
python -m enex2mela input/*.enex --categories categories.json
```

Free. Import `out/import.melarecipes` into Mela. Turn off Mela's iCloud sync while testing, because Mela has no bulk delete. Recipe IDs are stable across runs, so re-importing after a fix replaces recipes instead of duplicating them. Use `--limit 20` for a quick first look.

Open `out/report.csv`:

- `status` is `ok` (converted), `partial` (in Mela but needs a look), or `skipped` (not a recipe, empty, or a duplicate).
- `method` is `web`, `heuristic`, `ocr`, `llm`, or `unparsed`.
- `warnings` explains anything odd. OCR results always say "check quantities against the photo", because OCR misreads like `l cup` for `1 cup` happen.

## 4. Optional: send the leftovers to Claude

```sh
python -m enex2mela input/*.enex --categories categories.json --llm --llm-max-calls 25
```

Notes that parsed locally are handled exactly as before and never reach the API. Only `partial` ones are sent, and `--llm-max-calls` caps how many requests one run can make. The run ends with a token count and cost estimate. Answers are cached in `out/.cache/llm/`, so rerunning never pays twice for the same note.

Rough cost per note with a photo, at the default `low` effort: a few cents on `claude-opus-5-5` (the default), a fraction of a cent with `--model claude-haiku-5-5`. Try a handful first and compare quality before running the rest.

## Options

| Flag | What it does |
|---|---|
| `--triage-only` | Classify and report, convert nothing |
| `--llm` | Send notes that local parsing couldn't handle to Claude (off by default) |
| `--llm-max-calls N` | Stop calling Claude after N requests this run |
| `--no-ocr` | Don't OCR photos |
| `--no-fetch` | Don't re-fetch web clips; use the clipped text |
| `--categories FILE` | JSON map from tag/notebook names to Mela categories. `null` drops one. See `categories.example.json` |
| `--no-notebook-category` | Don't add the notebook name as a category |
| `--max-images N` | Images kept per recipe (default 2) |
| `--model`, `--effort` | Claude model (default `claude-opus-5-5`) and effort (default `low`) |
| `--workers N` | Notes converted in parallel (default 4) |
| `--limit N` | Only the first N notes |

## Tests

```sh
python -m pytest tests
```
