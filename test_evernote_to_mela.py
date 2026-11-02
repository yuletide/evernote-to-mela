import base64
import csv
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


def extracted(classification="recipe", recipes=None, warnings=None):
    if recipes is None:
        recipes = [{field: "" for field in converter.RECIPE_FIELDS}]
        recipes[0].update(title="Soup", ingredients="1 onion", instructions="Cook.")
    return {"classification": classification, "recipes": recipes, "warnings": warnings or []}


def enex(content="<div>1 onion</div><div>Cook.</div>", resources="", tags=""):
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


class ConverterTests(unittest.TestCase):
    def test_extract_html_tables_and_encrypted_content(self):
        note = ET.fromstring(enex(
            "<table><tr><td>1 cup</td><td>flour</td></tr></table>"
            "<en-crypt>encrypted</en-crypt>"
        )).find("note")
        result = converter.parse_note(note, 1)
        self.assertIn("1 cup\tflour", result["text"])
        self.assertTrue(result["encrypted"])
        self.assertNotIn("encrypted", result["text"])

    def test_api_payload_includes_images_pdf_and_no_remote_storage(self):
        note = converter.parse_note(ET.fromstring(enex(resources=[
            ("image/png", b"image"), ("application/pdf", b"pdf"),
            ("application/octet-stream", b"unsupported"),
        ])).find("note"), 1)
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
                         ["input_text", "input_image", "input_file"])
        self.assertTrue(parts[1]["image_url"].startswith("data:image/png;base64,"))
        self.assertTrue(parts[2]["file_data"].startswith("data:application/pdf;base64,"))

    def test_response_refusals_and_incomplete_results_fail(self):
        note = converter.parse_note(ET.fromstring(enex()).find("note"), 1)
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
        note = converter.parse_note(ET.fromstring(enex(
            resources=[("image/jpeg", b"img"), ("application/pdf", b"pdf")],
            tags=["Dinner", "bad,tag"],
        )).find("note"), 1)
        recipes, status, warnings = converter.convert_note(note, extracted())
        self.assertEqual(status, "converted")
        self.assertEqual(recipes[0]["images"], [base64.b64encode(b"img").decode()])
        self.assertEqual(recipes[0]["categories"], ["Dinner"])
        self.assertEqual(recipes[0]["link"], "https://example.com/recipe")
        self.assertTrue(any("application/pdf" in warning for warning in warnings))
        self.assertTrue(any("comma" in warning for warning in warnings))

    def test_unknown_and_inconsistent_results_do_not_silently_disappear(self):
        note = converter.parse_note(ET.fromstring(enex()).find("note"), 1)
        _, status, warnings = converter.convert_note(note, extracted("uncertain", []))
        self.assertEqual(status, "failed")
        self.assertTrue(warnings)
        with self.assertRaises(ValueError):
            converter.convert_note(note, extracted("not_recipe"))
        with self.assertRaises(ValueError):
            converter.convert_note(note, extracted("recipe", []))

    def test_duplicate_notes_have_stable_distinct_identifiers(self):
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
        first = ET.fromstring(enex()).find("note")
        second = ET.fromstring(ET.tostring(first))
        first.tail = "\n    "
        second.tail = "\n"
        self.assertEqual(converter.note_digest(first), converter.note_digest(second))

    def test_run_writes_archive_and_per_note_failure_report(self):
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
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.enex"
            for encoding in ("utf-8", "utf-16"):
                path.write_bytes('<!DOCTYPE en-export [<!ENTITY x "x">]><en-export/>'
                                 .encode(encoding))
                with self.assertRaisesRegex(ValueError, "entity declarations"):
                    list(converter.read_notes([path]))

    def test_api_auth_error_stops_subsequent_requests(self):
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
        self.assertEqual(converter.spreadsheet_safe(" =1+1"), "' =1+1")
        self.assertEqual(converter.spreadsheet_safe("@x"), "'@x")
        self.assertEqual(converter.spreadsheet_safe("Soup"), "Soup")

    def test_main_refuses_overwriting_or_same_input_and_output(self):
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
