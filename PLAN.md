# Evernote → Mela migration plan

> **Update:** the converter now runs local-first to avoid API costs. Photos go through on-device OCR (Apple Vision on a Mac, Tesseract elsewhere) and the same text parser as typed notes, which can now find ingredient lists without headings. Claude is opt-in (`--llm`) and only sees notes nothing local could parse. See the README for the current workflow; the sections below are the original plan.

The short version: export Evernote notebooks to `.enex`, run them through a
converter script that turns each note into a Mela recipe file, bundle those into
a `.melarecipes` archive, and import it into Mela in one go. The hard part isn't
the file formats. It's that Evernote notes are free-form, and Mela wants
ingredients and instructions as separate fields.

## 1. What we're working with

**Evernote export (`.enex`)** is XML. Each `<note>` carries:

- `<title>`, `<created>`, `<updated>`, zero or more `<tag>`
- `<note-attributes><source-url>` (set when the note came from the Web Clipper)
- `<content>`: the body as ENML (a restricted XHTML) inside CDATA
- `<resource>` blocks: attachments (photos, PDFs) as base64 `<data>` with a
  `<mime>` type, referenced from the body by `<en-media hash="...">` (MD5 of the
  data)

**Mela import** accepts `.melarecipe` (one recipe, JSON) and `.melarecipes` (a
zip of `.melarecipe` files). The JSON fields we care about:

| Mela field     | Type                  | Notes                                          |
|----------------|-----------------------|------------------------------------------------|
| `id`           | string                | any unique string; use a UUID                  |
| `title`        | string                |                                                |
| `text`         | string                | short description / intro                      |
| `ingredients`  | string                | one per line; `# Heading` lines make groups    |
| `instructions` | string                | one step per line; `# Heading` lines allowed   |
| `notes`        | string                | anything that doesn't fit elsewhere            |
| `yield`        | string                | "4 servings"                                   |
| `prepTime` / `cookTime` / `totalTime` | string | free text, "20 min"                  |
| `categories`   | array of strings      | map Evernote tags/notebooks here               |
| `images`       | array of strings      | base64-encoded JPEG/PNG                        |
| `link`         | string                | source URL                                     |
| `nutrition`    | string                |                                                |
| `favorite`, `wantToCook` | bool        |                                                |

Before writing the converter, export one or two recipes *from* Mela (share →
export) and diff them against this table. Mela's format isn't formally
documented, so a real file from the current app version is the reference, not
this list.

## 2. Get the data out of Evernote

Pick one:

1. **Evernote desktop app**: right-click each recipe notebook → *Export
   notebook* → ENEX. Check "include tags". Simple, but tedious if recipes are
   spread across many notebooks or a stack.
2. **[`evernote-backup`](https://github.com/vzhd1701/evernote-backup)** (Python
   CLI): `evernote-backup init-db`, `sync`, then `export ./enex/`. Pulls the
   whole account into one `.enex` per notebook. Better for large collections and
   avoids the desktop app's selection limits.

Either way, keep the raw `.enex` files untouched in `input/` and never edit
them. Every later step should be rerunnable from them.

## 3. Triage: sort notes by shape

Recipe notes in Evernote tend to fall into four buckets, and each needs a
different conversion path. A first script pass should just classify and count
them so we know the size of each bucket before investing in any of them.

| Bucket | How to detect | Conversion path |
|---|---|---|
| **A. Web clips with a live source URL** | `source-url` present, URL returns 200 | Fetch the page, read its schema.org `Recipe` JSON-LD (most recipe sites have it). Gives clean structured data for free. Fall back to B if the page has none. |
| **B. Typed or pasted text** | Text body, no dominant image | Parse the HTML: look for "Ingredients" / "Directions" / "Method" headings, lists, etc. Fall back to LLM extraction when heuristics fail. |
| **C. Photos/scans of cookbook pages or handwritten cards** | Body is mostly `<en-media>` images or a PDF | OCR + structuring. Evernote's own `<recognition>` data is too rough; send the image to a vision-capable LLM and ask for the Mela fields directly. Keep the original photo in `images`. |
| **D. Not actually a recipe** | Shopping lists, meal plans, "try this restaurant" | Skip, write to a report for manual review. |

Dead links in bucket A drop into B, because the clipped content is still in the
note body.

Confirmed mix for this collection: image scans, web captures, and written-out
recipes, so A, B and C all need building. For C, skip classic OCR (Tesseract):
it handles clean printed pages but falls apart on handwriting, multi-column
cookbook layouts, and photos taken at an angle. A vision LLM reads the image
and returns structured fields in one step. Notes with several images (a recipe
spanning two cookbook pages) get all their images sent in one request so the
model can stitch them together.

## 4. The converter

A Python script, roughly:

```
enex2mela.py input/*.enex --out out/ [--use-llm] [--fetch-urls]
```

Pipeline per note:

1. **Parse** the ENEX with `lxml` (streaming via `iterparse`, since exports with
   photos get large). Decode resources and index them by MD5 hash.
2. **Classify** into A/B/C/D (rules above).
3. **Extract** into an intermediate dict with Mela's fields:
   - A: `extruct` or a small JSON-LD reader; map `recipeIngredient`,
     `recipeInstructions` (handle `HowToSection` → `# Heading`), `recipeYield`,
     ISO-8601 durations (`PT1H30M` → "1 hr 30 min"), `image`.
   - B: heuristic section splitter on the HTML; if it can't find both an
     ingredient block and an instruction block, hand off to the LLM.
   - C: LLM with the image(s) attached, returning JSON matching the schema.
4. **Map metadata**:
   - `title` ← note title (strip "Recipe:" prefixes and clipper junk like
     " | Site Name").
   - `categories` ← Evernote tags + notebook name, run through a mapping table
     (`categories.yaml`) so "dessert", "Desserts", "sweets" collapse into one.
   - `link` ← `source-url`.
   - `images` ← first one or two image resources, resized to ~1600px long edge
     and re-encoded as JPEG to keep the archive small.
   - `notes` ← any leftover text the extractor didn't place, so nothing is
     silently dropped.
5. **Write** `out/recipes/<uuid>.melarecipe`, then zip all of them into
   `out/import.melarecipes`.
6. **Report**: `out/report.csv` with note title, bucket, extraction method,
   confidence, and warnings (no ingredients found, LLM used, image missing...).

For the LLM path, use a structured-output / tool-call schema so every response
is valid JSON in Mela's shape, and instruct it to copy text verbatim rather than
"improve" quantities or wording. Cache responses by note hash so reruns don't
re-bill.

## 5. Test on a sample before the full run

1. Hand-pick ~20 notes covering every bucket, including your messiest ones.
2. Convert, import into Mela, and look at them on the device.
3. Fix the mapping, rerun, repeat until the sample looks right.

Mela has no bulk delete, so test imports are easiest to undo on a second device
or a fresh iCloud account. Otherwise turn off Mela's iCloud sync while testing
so the junk doesn't spread to your other devices.

## 6. Full migration

1. Run the converter on everything.
2. Read `report.csv`, sorted by confidence. Fix low-confidence ones by hand
   (either in the generated JSON before import, or in Mela after).
3. Import `import.melarecipes` (AirDrop or Files → open with Mela on iOS, or
   File → Import on Mac).
4. Spot-check 10% at random against the original Evernote note.
5. Keep the Evernote account read-only for a few months before deleting
   anything. Keep the `.enex` files permanently, as they're the only
   lossless copy.

## 7. Things that will bite

- **Duplicates**: the same recipe clipped twice, or saved in two notebooks.
  Dedupe on normalized title + source URL before writing.
- **Ingredient lines with inline headers** ("For the sauce:") need to become
  `# For the sauce` lines, or Mela treats them as ingredients.
- **Tables**: some clipped sites lay ingredients out in `<table>`s. Flatten rows
  to lines.
- **Multiple recipes in one note** (e.g. "Thanksgiving 2019"). Detect multiple
  ingredient sections and either split or flag for manual handling.
- **Huge images**: a 12 MP phone photo as base64 in JSON bloats the archive and
  slows import. Resize.
- **Encrypted text blocks** (`<en-crypt>`) can't be read. Flag them.
- **Personal annotations** on clipped recipes ("used half the sugar, better")
  are worth keeping. Put them in `notes`, not lost when bucket A re-fetches from
  the source URL.

## 8. What I need from you to start

1. A rough count of recipe notes and how they're organized (one notebook,
   a stack, tags?).
2. One exported `.enex` with 10–20 representative recipes, including a few
   ugly ones.
3. One recipe exported from Mela as `.melarecipe`, as the format reference.
4. Whether you're OK sending recipe text/photos to an LLM API for buckets B
   and C, or want a heuristics-only run with manual cleanup.

With those, the order of work is: triage script → bucket A → bucket B →
bucket C → full run.
