# Evernote to Mela

Convert Evernote ENEX exports into a Mela `.melarecipes` archive using the
OpenAI API to extract recipe fields from note text, images and PDFs. See
[PLAN.md](PLAN.md) for the migration and verification plan.

## Before starting

- Python 3.9 or later and an OpenAI API key with access to a vision-capable
  model supporting structured outputs and PDF input are required. No Python
  packages need to be installed.
- This sends the entire selected note text and supported image and PDF
  attachments to OpenAI. API use may incur charges, including for image and
  PDF processing. Review current model pricing and OpenAI's data policies
  before running it. Requests set `store: false`.
- Validate the documented Mela archive format against a real Mela export and
  test a small synthetic archive in Mela before converting your notebook.
  The converter itself has not yet been validated against a real Mela import.
- Export the notebook as ENEX in Evernote's desktop app. Keep the original
  export and notebook: not all Evernote content can be represented in Mela.

## Convert a pilot

Set `OPENAI_API_KEY` in your environment without putting it into the repository,
then run:

```sh
python3 evernote_to_mela.py recipes.enex \
  --output pilot.melarecipes --report pilot.csv --limit 20
```

By default the model is `gpt-4o`; use `--model MODEL` to select another
compatible model. `--limit` processes the first N notes in export order, so
check that they are representative or create a separate pilot ENEX. Multiple
ENEX input paths are accepted. Omit `--limit` for the complete run; use new
output names because the script refuses to overwrite output files.

Review the CSV before importing the archive into a safe test library or
device. Each row has a source identifier, outcome, review flag, generated
recipe IDs, attachment counts and warnings. Failures cause a nonzero exit
status but the archive and report still contain results from other notes.
Authentication, permission, rate-limit and connection errors stop further API
requests; unattempted notes are reported as failed. Fix failures and rerun
into new files; there is no automatic retry or resume. Report cells beginning
with spreadsheet formula characters are prefixed with an apostrophe.
Avoid importing both a pilot and a full run into the same library without
first checking how Mela handles existing recipe IDs.

The script uses archived note content, not live websites. It includes JPEG,
PNG, GIF and WebP resources as Mela images, and sends PDFs to the model for
extraction but does not embed PDFs in Mela. Other attachments, encrypted
blocks, missing fields, uncertain classifications and multi-recipe notes are
flagged for review. Image attachments on multi-recipe notes are copied to
each recipe. Preserve the ENEX to retain originals and unsupported content.
Exact duplicate source notes are flagged, but similar recipes are not
automatically detected. Source IDs are based on the exported note content, so
editing a note changes its ID; identical notes receive occurrence numbers.
Archive image counts are not proof that Mela imported those images.
An LLM can misread or invent details even when asked not to: check results
against the originals before relying on them.

Keep ENEX files, archives, reports and API credentials private and out of git.

## Run offline tests

```sh
python3 -m unittest -v
```
