import base64
import csv
import hashlib
import http.client
import io
import json
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import evernote_to_mela as converter
import pymupdf
from PIL import Image


def extracted(classification="recipe", recipes=None, warnings=None):
    """Create a valid structured extraction fixture."""
    if recipes is None:
        recipes = [{field: "" for field in converter.RECIPE_FIELDS}]
        recipes[0].update(title="Soup", ingredients="1 onion", instructions="Cook.",
                          preferredImageIndex=0)
    return {"classification": classification, "recipes": recipes, "warnings": warnings or []}


def enex(content="<div>1 onion</div><div>Cook.</div>", resources="", tags=""):
    """Build an ENEX document containing one configurable note."""
    root = ET.Element("en-export")
    note = ET.SubElement(root, "note")
    ET.SubElement(note, "title").text = "Soup"
    ET.SubElement(note, "content").text = f"<en-note>{content}</en-note>"
    attrs = ET.SubElement(note, "note-attributes")
    ET.SubElement(attrs, "source-url").text = "https://example.com/recipe"
    for tag in tags:
        ET.SubElement(note, "tag").text = tag
    for mime, data in resources:
        resource = ET.SubElement(note, "resource")
        ET.SubElement(resource, "mime").text = mime
        ET.SubElement(resource, "data", encoding="base64").text = base64.b64encode(data).decode()
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def image_bytes(format="PNG", color="red"):
    """Create a small in-memory image."""
    output = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(output, format=format)
    return output.getvalue()


def pdf_bytes():
    """Create an in-memory two-page recipe document."""
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "First recipe")
    document.new_page().insert_text((72, 72), "Second recipe")
    data = document.tobytes()
    document.close()
    return data


def prepared(content="<div>1 onion</div><div>Cook.</div>", resources=(), tags=()):
    """Parse a test note and prepare its image assets."""
    note = converter.parse_note(ET.fromstring(enex(content, resources, tags)).find("note"), 1)
    note["images"], note["image_warnings"] = converter.prepare_images(note)
    return note


class ConverterTests(unittest.TestCase):
    """Exercise conversion, reporting, media handling, and service failures."""

    def test_extract_html_tables_and_encrypted_content(self):
        """Flatten table cells without exposing encrypted content."""
        note = ET.fromstring(enex(
            "<table><tr><td>1 cup</td><td>flour</td></tr></table>"
            "<en-crypt>encrypted</en-crypt>"
        )).find("note")
        result = converter.parse_note(note, 1)
        self.assertIn("1 cup\tflour", result["text"])
        self.assertTrue(result["encrypted"])
        self.assertNotIn("encrypted", result["text"])

    def test_api_payload_includes_images_pdf_and_no_remote_storage(self):
        """Send image and PDF inputs with structured output and storage disabled."""
        note = prepared(resources=[
            ("image/png", image_bytes()), ("application/pdf", pdf_bytes()),
            ("application/octet-stream", b"unsupported"),
        ])
        api_response = {
            "status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(extracted())}]}],
        }
        with patch("evernote_to_mela.urllib.request.urlopen",
                   return_value=io.BytesIO(json.dumps(api_response).encode())) as urlopen:
            converter.request_extraction(note, "secret", "gpt-4o")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
        parts = payload["input"][0]["content"]
        self.assertEqual([p["type"] for p in parts],
                         ["input_text", "input_text", "input_image", "input_text",
                          "input_image", "input_text", "input_image", "input_file"])
        self.assertTrue(parts[2]["image_url"].startswith("data:image/png;base64,"))
        self.assertTrue(parts[-1]["file_data"].startswith("data:application/pdf;base64,"))

    def test_response_refusals_and_incomplete_results_fail(self):
        """Reject refusals and incomplete service responses."""
        note = prepared()
        for result in [
            {"status": "incomplete", "output": []},
            {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "refusal", "refusal": "no"}]}]},
        ]:
            with self.subTest(result=result), patch(
                "evernote_to_mela.urllib.request.urlopen",
                return_value=io.BytesIO(json.dumps(result).encode())
            ):
                with self.assertRaises(ValueError):
                    converter.request_extraction(note, "secret", "gpt-4o")

    def test_conversion_preserves_images_and_reports_unsupported(self):
        """Keep photo and PDF page images while reporting conversion caveats."""
        image = image_bytes("JPEG")
        note = prepared(
            resources=[("image/jpeg", image), ("application/pdf", pdf_bytes())],
            tags=["Dinner", "bad,tag"],
        )
        recipes, status, warnings = converter.convert_note(note, extracted())
        self.assertEqual(status, "converted")
        self.assertEqual(recipes[0]["images"][0], base64.b64encode(image).decode())
        self.assertEqual(len(recipes[0]["images"]), 3)
        self.assertEqual(recipes[0]["categories"], ["Dinner"])
        self.assertEqual(recipes[0]["link"], "https://example.com/recipe")
        self.assertTrue(any("PDF pages saved" in warning for warning in warnings))
        self.assertTrue(any("comma" in warning for warning in warnings))

    def test_unknown_and_inconsistent_results_do_not_silently_disappear(self):
        """Report uncertain notes and reject contradictory classifications."""
        note = prepared()
        _, status, warnings = converter.convert_note(note, extracted("uncertain", []))
        self.assertEqual(status, "failed")
        self.assertTrue(warnings)
        with self.assertRaises(ValueError):
            converter.convert_note(note, extracted("not_recipe"))
        with self.assertRaises(ValueError):
            converter.convert_note(note, extracted("recipe", []))

    def test_book_photos_heic_pdf_and_lead_image_are_retained(self):
        """Preserve photos and pages while selecting per-recipe lead images."""
        first = image_bytes("JPEG", "red")
        heic = image_bytes("HEIF", "blue")
        note = prepared(resources=[
            ("image/jpeg", first), ("image/heic", heic), ("application/pdf", pdf_bytes()),
        ])
        self.assertEqual(len(note["images"]), 4)
        with Image.open(io.BytesIO(note["images"][1][1])) as converted:
            self.assertEqual(converted.format, "PNG")
        one = extracted()["recipes"][0]
        two = dict(one, title="Another", preferredImageIndex=2)
        one["preferredImageIndex"] = 1
        recipes, status, warnings = converter.convert_note(note, extracted(recipes=[one, two]))
        self.assertEqual(status, "converted")
        self.assertEqual(len(recipes[0]["images"]), 4)
        self.assertEqual(len(recipes[1]["images"]), 4)
        self.assertEqual(recipes[1]["images"][0],
                         base64.b64encode(note["images"][1][1]).decode())
        self.assertEqual(set(recipes[0]["images"]), set(recipes[1]["images"]))
        self.assertTrue(any("Multiple recipes" in warning for warning in warnings))

    def test_missing_and_invalid_lead_images_are_flagged(self):
        """Flag missing images and out-of-range lead image choices."""
        note = prepared()
        recipes, _, warnings = converter.convert_note(note, extracted())
        self.assertEqual(recipes[0]["images"], [])
        self.assertTrue(any("no source image" in warning for warning in warnings))
        note = prepared(resources=[("image/png", image_bytes())])
        result = extracted()
        result["recipes"][0]["preferredImageIndex"] = 2
        recipes, _, warnings = converter.convert_note(note, result)
        self.assertEqual(len(recipes[0]["images"]), 1)
        self.assertTrue(any("invalid preferred" in warning for warning in warnings))

    def test_inline_links_are_preserved_in_extraction_and_mela_notes(self):
        """Retain inline link labels and destinations in extracted recipes."""
        note = prepared(content='<div>See <a href="https://example.com/sauce">my '
                                '<b>sauce</b></a>.</div>')
        self.assertIn("my sauce (https://example.com/sauce)", note["text"])
        recipes, _, _ = converter.convert_note(note, extracted())
        self.assertIn("my sauce (https://example.com/sauce)", recipes[0]["notes"])

    def test_inline_embedded_and_remote_images_are_accounted_for(self):
        """Import data-URI images and flag remote images without fetching them."""
        photo = image_bytes()
        encoded = base64.b64encode(photo).decode()
        note = prepared(content=f'<img src="data:image/png;base64,{encoded}">'
                                '<img src="https://example.com/picture.png">')
        self.assertEqual(len(note["images"]), 1)
        self.assertEqual(note["resource_count"], 1)
        recipes, _, warnings = converter.convert_note(note, extracted())
        self.assertEqual(recipes[0]["images"], [encoded])
        self.assertIn("https://example.com/picture.png", recipes[0]["notes"])
        self.assertTrue(any("inline image reference" in warning for warning in warnings))

    def test_unreadable_book_photo_and_pdf_are_reported(self):
        """Do not silently discard malformed image and PDF attachments."""
        note = prepared(resources=[("image/heic", b"bad"), ("application/pdf", b"bad")])
        _, _, warnings = converter.convert_note(note, extracted())
        self.assertTrue(any("could not convert" in warning for warning in warnings))
        self.assertTrue(any("could not render" in warning for warning in warnings))
        self.assertTrue(any("no source image" in warning for warning in warnings))

    def test_enex_image_references_are_reconciled(self):
        """Report a referenced ENEX image only when its resource is missing."""
        photo = image_bytes()
        digest = hashlib.md5(photo, usedforsecurity=False).hexdigest()
        content = f'<en-media type="image/png" hash="{digest}"/>'
        present = prepared(content=content, resources=[("image/png", photo)])
        missing = prepared(content=content)
        self.assertFalse(any("missing" in error for error in present["errors"]))
        self.assertTrue(any("attachment is missing" in error for error in missing["errors"]))

    def test_cmyk_scan_is_converted_to_png(self):
        """Convert image modes unsupported by PNG instead of dropping a scan."""
        output = io.BytesIO()
        Image.new("CMYK", (4, 4), (0, 0, 0, 0)).save(output, format="TIFF")
        note = prepared(resources=[("image/tiff", output.getvalue())])
        self.assertEqual(len(note["images"]), 1)
        with Image.open(io.BytesIO(note["images"][0][1])) as image:
            self.assertEqual(image.format, "PNG")

    def test_malformed_api_response_is_a_note_failure(self):
        """Reject malformed response envelopes without aborting the batch."""
        note = prepared()
        malformed = [None, [], {"status": "completed", "output": None},
                     {"status": "completed", "output": [None, {"type": "message",
                      "content": [None, {"type": "output_text"}]}]}]
        for result in malformed:
            with self.subTest(result=result), patch(
                "evernote_to_mela.urllib.request.urlopen",
                return_value=io.BytesIO(json.dumps(result).encode())
            ):
                with self.assertRaises(ValueError):
                    converter.request_extraction(note, "secret", "gpt-4o")

    def test_response_read_errors_stop_requests(self):
        """Treat truncated reads and timeouts as batch-stopping failures."""
        note = prepared()
        response = io.BytesIO(b"")
        for error in (http.client.IncompleteRead(b"partial"), TimeoutError()):
            with self.subTest(error=error), patch(
                "evernote_to_mela.urllib.request.urlopen", return_value=response
            ), patch("evernote_to_mela.json.load", side_effect=error):
                with self.assertRaises(converter.ApiError) as raised:
                    converter.request_extraction(note, "secret", "gpt-4o")
                self.assertTrue(raised.exception.stop)
            response = io.BytesIO(b"")

    def test_truncated_read_marks_later_notes_unattempted(self):
        """Record all notes and stop API calls after a truncated response."""
        root = ET.fromstring(enex())
        root.append(ET.fromstring(ET.tostring(root.find("note"))))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.enex"
            archive = Path(directory) / "out.melarecipes"
            report = Path(directory) / "report.csv"
            path.write_bytes(ET.tostring(root))
            with patch("evernote_to_mela.urllib.request.urlopen",
                       return_value=io.BytesIO(b"")) as request, patch(
                "evernote_to_mela.json.load", side_effect=http.client.IncompleteRead(b"partial")
            ):
                status = converter.run([path], archive, report, "secret", "gpt-4o")
            self.assertEqual(status, 1)
            self.assertEqual(request.call_count, 1)
            with report.open(newline="") as output:
                rows = list(csv.DictReader(output))
            self.assertEqual([row["status"] for row in rows], ["failed", "failed"])
            self.assertIn("Not attempted", rows[1]["warnings"])

    def test_archive_contains_original_photo_and_pdf_page_images(self):
        """Write every source photo and rendered PDF page into the Mela archive."""
        photo = image_bytes("JPEG")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.enex"
            archive = Path(directory) / "out.melarecipes"
            report = Path(directory) / "report.csv"
            path.write_bytes(enex(resources=[("image/jpeg", photo),
                                            ("application/pdf", pdf_bytes())]))
            with patch("evernote_to_mela.request_extraction", return_value=extracted()):
                self.assertEqual(converter.run([path], archive, report, "secret", "gpt-4o"), 0)
            with zipfile.ZipFile(archive) as output:
                recipe = json.loads(output.read(output.namelist()[0]))
            self.assertEqual(len(recipe["images"]), 3)
            self.assertEqual(base64.b64decode(recipe["images"][0]), photo)
            for encoded in recipe["images"][1:]:
                with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
                    self.assertEqual(image.format, "PNG")

    def test_duplicate_notes_have_stable_distinct_identifiers(self):
        """Give duplicate notes distinct but reproducible identifiers."""
        root = ET.fromstring(enex())
        root.append(ET.fromstring(ET.tostring(root.find("note"))))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.enex"
            path.write_bytes(ET.tostring(root))
            first = list(converter.read_notes([path]))
            second = list(converter.read_notes([path]))
        self.assertNotEqual(first[0]["id"], first[1]["id"])
        self.assertEqual([n["id"] for n in first], [n["id"] for n in second])

    def test_source_id_ignores_whitespace_after_note(self):
        """Exclude surrounding ENEX whitespace from source note IDs."""
        first = ET.fromstring(enex()).find("note")
        second = ET.fromstring(ET.tostring(first))
        first.tail = "\n    "
        second.tail = "\n"
        self.assertEqual(converter.note_digest(first), converter.note_digest(second))

    def test_run_writes_archive_and_per_note_failure_report(self):
        """Write successful recipes and report errors for every source note."""
        root = ET.fromstring(enex())
        root.append(ET.fromstring(ET.tostring(root.find("note"))))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.enex"
            archive = Path(directory) / "out.melarecipes"
            report = Path(directory) / "report.csv"
            path.write_bytes(ET.tostring(root))
            with patch("evernote_to_mela.request_extraction",
                       side_effect=[extracted(), RuntimeError("API error")]):
                status = converter.run([path], archive, report, "secret", "gpt-4o")
            self.assertEqual(status, 1)
            with zipfile.ZipFile(archive) as output:
                self.assertEqual(len(output.namelist()), 1)
                recipe = json.loads(output.read(output.namelist()[0]))
                self.assertEqual(recipe["ingredients"], "1 onion")
                self.assertTrue(recipe["id"])
            with report.open(newline="") as output:
                rows = list(csv.DictReader(output))
            self.assertEqual([row["status"] for row in rows], ["converted", "failed"])
            self.assertEqual(rows[1]["review_required"], "yes")
            self.assertIn("API error", rows[1]["warnings"])
            self.assertTrue(all("Exact duplicate" in row["warnings"] for row in rows))

    def test_rejects_entity_declarations_before_api_calls(self):
        """Reject entity declarations in UTF-8 and UTF-16 exports."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.enex"
            for encoding in ("utf-8", "utf-16"):
                path.write_bytes('<!DOCTYPE en-export [<!ENTITY x "x">]><en-export/>'
                                 .encode(encoding))
                with self.assertRaisesRegex(ValueError, "entity declarations"):
                    list(converter.read_notes([path]))

    def test_api_auth_error_stops_subsequent_requests(self):
        """Stop further requests after an authentication failure."""
        root = ET.fromstring(enex())
        root.append(ET.fromstring(ET.tostring(root.find("note"))))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.enex"
            archive = Path(directory) / "out.melarecipes"
            report = Path(directory) / "report.csv"
            path.write_bytes(ET.tostring(root))
            with patch("evernote_to_mela.request_extraction",
                       side_effect=converter.ApiError("HTTP 401", stop=True)) as request:
                status = converter.run([path], archive, report, "secret", "gpt-4o")
            self.assertEqual(status, 1)
            self.assertEqual(request.call_count, 1)
            with report.open(newline="") as output:
                rows = list(csv.DictReader(output))
            self.assertEqual([row["status"] for row in rows], ["failed", "failed"])
            self.assertIn("Not attempted", rows[1]["warnings"])

    def test_spreadsheet_formula_values_are_escaped(self):
        """Prevent formula execution when reports are opened in a spreadsheet."""
        self.assertEqual(converter.spreadsheet_safe(" =1+1"), "' =1+1")
        self.assertEqual(converter.spreadsheet_safe("@x"), "'@x")
        self.assertEqual(converter.spreadsheet_safe("Soup"), "Soup")

    def test_main_refuses_overwriting_or_same_input_and_output(self):
        """Protect existing files and reject overlapping input/output paths."""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.enex"
            output = Path(directory) / "output.melarecipes"
            source.write_bytes(enex())
            output.write_text("existing")
            with patch.dict("os.environ", {"OPENAI_API_KEY": "secret"}):
                with self.assertRaises(SystemExit):
                    converter.main([str(source), "--output", str(output),
                                    "--report", str(source)])
                with self.assertRaises(SystemExit):
                    converter.main([str(source), "--output", str(output),
                                    "--report", str(Path(directory) / "report.csv")])
            self.assertEqual(output.read_text(), "existing")


if __name__ == "__main__":
    unittest.main()
