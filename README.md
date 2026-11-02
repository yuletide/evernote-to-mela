# Evernote to Mela

Convert Evernote ENEX exports into a Mela `.melarecipes` archive using the
OpenAI API to extract recipe fields from note text, images and PDFs. See
[PLAN.md](PLAN.md) for the migration and verification plan.

## Before starting

- Python 3.9 or later and an OpenAI API key with access to a vision-capable
  model supporting structured outputs and PDF input are required. Install the
  image and PDF dependencies in a virtual environment:

  ```sh
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  ```
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
.venv/bin/python evernote_to_mela.py recipes.enex \
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

The script uses archived note content, not live websites. It preserves JPEG,
PNG and WebP attachments as Mela images, converts other readable image formats
(including HEIC and GIF) to PNG, and renders every PDF page as a PNG image.
The original PDF is also sent to the model, but cannot be embedded as a PDF in
Mela. All source images and rendered PDF pages are attached to every recipe
from the note so cookbook photos are not discarded. The model picks a lead
image for each recipe when it can; review its choice, especially for notes
containing multiple recipes. This can duplicate images and increase archive
size, API usage and cost.

Inline image links and unsupported or unreadable attachments are flagged; the
script does not download images from source websites. Recipes without source
images are flagged rather than given an invented image. For those recipes,
open the saved source link in Mela to find an image or use Mela's web import;
check for duplicates and preserve your Evernote annotations when doing so.
Encrypted blocks, missing fields, uncertain classifications and multi-recipe
notes are also flagged. Preserve the ENEX to retain original attachments and
unsupported content.
Exact duplicate source notes are flagged, but similar recipes are not
automatically detected. Source IDs are based on the exported note content, so
editing a note changes its ID; identical notes receive occurrence numbers.
Archive image counts are not proof that Mela imported those images.
An LLM can misread or invent details even when asked not to: check results
against the originals before relying on them.

Keep ENEX files, archives, reports and API credentials private and out of git.

## Run offline tests

```sh
.venv/bin/python -m unittest -v
```
