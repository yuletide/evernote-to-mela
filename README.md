# evernote-to-mela

Converts Evernote recipe notes into a `.melarecipes` file that Mela imports in one go. See [PLAN.md](PLAN.md) for the reasoning behind the approach.

Each note is sorted into one of four buckets and converted accordingly:

| Bucket | What it is | How it's converted |
|---|---|---|
| `web` | Web Clipper notes with a source URL | Re-fetches the page and reads its schema.org recipe data. Falls back to the clipped text if the page is gone. |
| `text` | Typed or pasted recipes | Splits on "Ingredients" / "Directions" headings. Falls back to Claude if there aren't any. |
| `image` | Photos, scans, PDFs, handwritten cards | Claude reads the images and fills in the fields. |
| `empty` | Nothing in it | Skipped. |

Claude also decides when a note isn't a recipe (shopping lists, meal plans) and skips it. Anything that can't be parsed is still written to Mela with the original text in its notes field, so nothing gets lost, and it's flagged in the report.

## Setup

```sh
pip install -r requirements.txt
pip install pillow-heif            # optional: needed if your photos are HEIC
export ANTHROPIC_API_KEY=sk-ant-...
```

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

Prints how many notes fall into each bucket and writes `out/report.csv`. No API calls, no files written.

## 3. Test run

```sh
python -m enex2mela input/*.enex --limit 20
```

Import `out/import.melarecipes` into Mela and look at the results. Turn off Mela's iCloud sync while testing, because Mela has no bulk delete. Recipe IDs are stable across runs, so re-importing after a fix replaces recipes instead of duplicating them.

## 4. Full run

```sh
python -m enex2mela input/*.enex --categories categories.json
```

Then open `out/report.csv` and sort by `status`:

- `ok`: converted cleanly.
- `partial`: written to Mela but needs a look (missing ingredients or steps, or nothing could parse it).
- `skipped`: not a recipe, empty, or a duplicate.
- `warnings`: explains why.

Claude's answers are cached in `out/.cache/llm/`, so rerunning after a tweak doesn't call (or bill) the API again for notes that haven't changed.

## Options

| Flag | What it does |
|---|---|
| `--triage-only` | Classify and report, convert nothing |
| `--no-llm` | Skip Claude; photos and messy notes get flagged instead |
| `--no-fetch` | Don't re-fetch web clips; use the clipped text |
| `--categories FILE` | JSON map from tag/notebook names to Mela categories. `null` drops one. See `categories.example.json` |
| `--no-notebook-category` | Don't add the notebook name as a category |
| `--max-images N` | Images kept per recipe (default 2) |
| `--model`, `--effort` | Claude model (default `claude-opus-5-5`) and effort (default `medium`) |
| `--workers N` | Notes converted in parallel (default 4) |
| `--limit N` | Only the first N notes |

## Tests

```sh
python -m pytest tests
```
