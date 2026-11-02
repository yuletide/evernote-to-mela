# Evernote → Mela migration plan

The hard part is the content, not the file formats. Evernote exports `.enex` (XML; HTML body, base64 attachments). Mela imports `.melarecipes` (a zip of JSON files, to be confirmed against a real Mela export). Mela wants ingredients, instructions, yield and times as separate fields, while Evernote notes are free-form.

## 1. Audit
- Count recipe notes; collect them in one notebook or tag.
- Classify: web clips, typed/pasted text, photos/PDFs/scans, non-recipes.

## 2. Export
- Evernote desktop: select notes → File → Export Notes → ENEX, or use `evernote-backup` for many notebooks.
- Keep the `.enex` files permanently as the lossless backup.

## 3. Handle each note type
1. **Web clips with a live source URL:** re-fetch the page and read its schema.org Recipe data. Put the user's own annotations in Mela's `notes` field.
2. **Typed/pasted recipes:** split on headings such as "Ingredients" and "Directions"; fall back to an LLM for field extraction.
3. **Photos/scans:** a vision model returns Mela's fields; keep the original photo attached.
4. **Non-recipes** (shopping lists, meal plans): skip and list in `report.csv`.

## 4. Convert
A Python script handles all four types, writes one importable archive, and emits `report.csv` flagging low-confidence conversions.

## 5. Pilot
- Convert ~20 representative notes.
- Import with Mela's iCloud sync off. Mela has no bulk delete, so bad test imports would otherwise sync to other devices.

## 6. Full run
- Convert everything, review flagged rows, import, then spot-check about 10% against the originals.

## 7. Verify
- Compare recipe counts between Evernote and Mela.
- Keep Evernote untouched for 2–4 weeks.

## Known traps
Duplicate recipes, "For the sauce:" sub-headings, ingredients in tables, multiple recipes in one note, oversized phone photos, encrypted text blocks.

## Inputs needed
- Approximate note count, and whether they are organized by notebook or tag.
- A sample `.enex` of 10–20 typical recipes, including messy ones.
- One recipe exported from Mela, to match its exact format.
- Whether recipe text and photos may be sent to an LLM (otherwise rules only, more manual cleanup).
