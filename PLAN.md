# Evernote to Mela migration plan

## Scope and format

Migrate approximately 200 recipe notes from one Evernote notebook. The user
has approved sending recipe text and photos to an LLM for extraction.

Mela's [file format documentation](https://mela.recipes/fileformat/index.html)
describes individual `.melarecipe` files as JSON and `.melarecipes` as a ZIP
of these files. It documents fields including a nonempty `id`, `title`,
`images` (base64-encoded images), `categories`, `yield`, `prepTime`,
`cookTime`, `totalTime`, `ingredients`, `instructions`, `notes`, and `link`.
Ingredients and instructions are strings with newline-separated entries,
not arrays. The [Mela help](https://mela.recipes/help/) and the user's
screenshots confirm that Mela imports `.melarecipes`. Validate the documented
format against a real Mela export before conversion, and confirm import
behavior in a pilot.

Mela's help says its Paprika importer does not support links to other recipes
or images embedded in text; do not assume arbitrary Evernote attachments or
inline layout will survive. Retain the original ENEX as the authoritative
backup and report any content that cannot be represented.

## 0. Check import compatibility

Before converting notes, obtain a real
`.melarecipes` export from Mela and inspect its ZIP members and recipe JSON
fields against the documented format. Then create a small synthetic archive
using the verified layout and import it into a safe test library or device.
Check the title, ingredients, instructions, image and other populated fields.
Resolve any format or import problems before conversion and the pilot.

## 1. Export and inventory

- In Evernote's desktop app, export the recipe notebook as ENEX, including
  available note attributes and attachments. Use multiple files if needed.
- Keep the original export unchanged and private. Inventory note count,
  titles, source URLs, tags, encrypted blocks, and attachment types.
- Use a small representative sample for development, including web clips,
  typed recipes, images, PDFs, tables and notes with multiple recipes.

## 2. Convert

Use the Python converter to read ENEX and produce a `.melarecipes` archive
and a per-note `report.csv`:

1. Extract the note text, source URL, tags and attachments. Preserve original
   note content and attachments in the ENEX backup.
2. Use an LLM for text extraction and a vision-capable model for photos or
   scans. PDFs require supported page extraction or rendering; flag anything
   that cannot be processed. Re-fetching a source page and using its
   schema.org data could help, but the converter does not fetch live pages:
   it uses the archived note body and attachments, and flags unresolved
   clips for manual review. If live-page enrichment is added later, use it
   only to check or enrich the archived content and record fetch failures and
   content mismatches in the report.
3. Generate documented Mela fields. Preserve section headings, quantities,
   units and instructions; do not invent missing information. Include the
   source link and user annotations. Put supported recipe images in the
   `images` field; explicitly report other attachments or omitted content.
4. Flag non-recipes, multiple recipes in one note, exact duplicate notes,
   encrypted text, missing fields, unreadable images, model uncertainty and
   processing failures for review. Similar recipes may still need manual
   duplicate detection. Do not silently discard
   notes. Record each source note's stable identifier and outcome (converted,
   skipped or failed), manual-review status and note-to-recipe mappings so
   retries and reconciliation are possible.

The converter should make its chosen LLM provider and costs clear, allow
credentials to be supplied securely, and avoid logging recipe content by
default. The user's consent applies to recipe text and photos; handle other
attachment types only when supported and appropriate.

## 3. Pilot

- Convert approximately 20 varied notes, including difficult examples, and
  review the report alongside the originals.
- Import into a safe test library or device after confirming how to prevent
  test recipes from affecting the existing synced collection. Check the
  actual Mela import, image handling, annotations, categories and formatting.
- Correct problems before proceeding. Do not assume disabling sync or
  removing a bad batch is safe without verifying it on the user's setup.

## 4. Full migration and verification

- Convert the complete notebook, review every flagged result, and import
  in manageable batches.
- Reconcile all roughly 200 source notes against the report, including
  skipped and failed notes and notes containing multiple recipes. Compare
  source and imported attachment counts, accounting for unsupported or
  omitted attachments in the report. Spot-check at least 10% of imported
  recipes against the originals, including photos, ingredient quantities
  and directions.
- Keep the ENEX files and original Evernote notebook until the migration is
  verified; retain the exports as the lossless backup.

## Next input

First provide a `.melarecipes` export from Mela for the compatibility check.
Then provide the notebook's ENEX export, or a 10–20-note representative export
to start the pilot. Do not commit recipe exports, attachments, model credentials,
or reports containing private recipe content to the repository.
