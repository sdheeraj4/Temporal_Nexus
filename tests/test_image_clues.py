"""Mocked OCR contract/error checks and separately marked real native OCR checks."""

from functools import partial
from io import BytesIO
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from PIL import Image, ImageDraw, ImageFont
import pytesseract

from backend.models import TextContext
from backend.services import image_clues as ocr
import test_input as inputs

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
TSV = HEADER + (
    "5\t1\t1\t1\t1\t1\t1\t1\t4\t3\t96.5\tNEXUS\n"
    "5\t1\t1\t1\t1\t2\t6\t1\t4\t3\t91\tDEMO\n"
    "5\t1\t1\t1\t2\t1\t1\t5\t4\t3\t89\t2026\n"
)


def demo_image(blank=False):
    """A generated, non-personal image; no files are retained."""
    output = BytesIO()
    with Image.new("RGB", (1100, 240), "white") as image:
        if not blank:
            ImageDraw.Draw(image).text((40, 60), "NEXUS DEMO 2026", fill="black", font=ImageFont.load_default(size=80))
        image.save(output, format="PNG")
    return output.getvalue()


class OCRHTTPTests(unittest.TestCase):
    setUpClass = classmethod(inputs.InputEndpointTests.setUpClass.__func__)
    tearDownClass = classmethod(inputs.InputEndpointTests.tearDownClass.__func__)

    def post(self, image, fields=None):
        return inputs.InputEndpointTests.post(self, image, fields, path="/api/image-clues")

    def test_mocked_observations_and_separate_context(self):
        with patch.object(ocr, "check_engine"), patch.object(ocr.pytesseract, "run_and_get_output", return_value=TSV) as engine:
            status, body = self.post(self.png, {"consent_confirmed": "true", "name": "  Supplied Only  "})
        self.assertEqual(status, 200)
        self.assertEqual(body["supplied_context"]["name"], "Supplied Only")
        self.assertEqual(body["extracted_text"], "NEXUS DEMO\n2026")
        self.assertNotIn("Supplied Only", body["extracted_text"])
        self.assertEqual(body["ocr_status"], "completed")
        self.assertEqual(body["tokens"][0]["ocr_confidence"], 96.5)
        self.assertEqual(body["tokens"][0]["bounding_box"], {"left": 1, "top": 1, "width": 4, "height": 3})
        self.assertEqual(body["processed_image"]["width"], 16)
        self.assertIn("unverified", body["message"])
        self.assertIn("not identity confidence", body["message"])
        self.assertEqual(engine.call_args.kwargs["lang"], "eng")
        self.assertGreater(engine.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(engine.call_args.kwargs["timeout"], 15)

    def test_mocked_empty_observations(self):
        with patch.object(ocr, "check_engine"), patch.object(ocr.pytesseract, "run_and_get_output", return_value=HEADER):
            status, body = self.post(self.png)
        self.assertEqual(status, 200)
        self.assertEqual(body["ocr_status"], "no_text")
        self.assertEqual(body["extracted_text"], "")
        self.assertEqual(body["tokens"], [])

    def test_mocked_missing_engine_and_other_routes(self):
        with patch.object(ocr.shutil, "which", return_value=None):
            status, body = self.post(self.png)
            validation_status, _ = inputs.InputEndpointTests.post(self, self.png)
            with urlopen(self.base_url + "/api/health", timeout=5) as health:
                self.assertEqual(health.status, 200)
        self.assertEqual(validation_status, 200)
        self.assertEqual(status, 503)
        self.assertEqual(body["detail"]["code"], "ocr_engine_missing")
        self.assertNotIn("ocr_status", body)

    def test_mocked_missing_english_data(self):
        result = subprocess.CompletedProcess([], 0, stdout="List of languages (1):\nfra\n", stderr="")
        with patch.object(ocr.shutil, "which", return_value="tesseract"), patch.object(ocr.subprocess, "run", return_value=result):
            status, body = self.post(self.png)
        self.assertEqual(status, 503)
        self.assertEqual(body["detail"]["code"], "ocr_language_missing")

    def test_actual_local_engine_availability_response(self):
        # No native mocks: verify the API honestly reflects this machine's setup.
        try:
            ocr.check_engine()
        except ocr.OCRError as error:
            status, body = self.post(demo_image(blank=True))
            self.assertEqual(status, error.status_code)
            self.assertEqual(body["detail"]["code"], error.code)
            self.assertNotIn("ocr_status", body)
        else:
            status, body = self.post(demo_image(blank=True))
            self.assertEqual(status, 200)
            self.assertEqual(body["ocr_status"], "no_text")

    def test_mocked_timeout_and_engine_failure(self):
        for error, expected_status, code in (
            (RuntimeError("Tesseract process timeout"), 504, "ocr_timeout"),
            (pytesseract.TesseractError(1, "private text must not leak"), 500, "ocr_failed"),
        ):
            with self.subTest(code=code), patch.object(ocr, "check_engine"), patch.object(ocr.pytesseract, "run_and_get_output", side_effect=error):
                status, body = self.post(self.png)
            self.assertEqual(status, expected_status)
            self.assertEqual(body["detail"]["code"], code)
            self.assertNotIn("private text", str(body))
            self.assertNotIn("ocr_status", body)

    def test_invalid_input_never_reaches_ocr(self):
        cases = [
            (None, None, 422), (b"", None, 400), (b"not an image", None, 415),
            (self.png[:-15], None, 400),
            (self.png + b"x" * inputs.MAX_IMAGE_BYTES, None, 413),
            (inputs.generated_image(size=(5000, 4001)), None, 413),
            (self.png, {}, 422), (self.png, {"consent_confirmed": "false"}, 422),
            (self.png, {"consent_confirmed": "true", "name": "x" * 201}, 422),
        ]
        with patch("backend.main.extract_image_clues") as engine:
            for image, fields, expected in cases:
                with self.subTest(expected=expected, fields=fields):
                    status, _ = self.post(image, fields)
                    self.assertEqual(status, expected)
            engine.assert_not_called()

    def test_blocking_ocr_does_not_block_health(self):
        started, release = threading.Event(), threading.Event()
        result = []

        def slow_ocr(*args, **kwargs):
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError("timeout")
            return HEADER

        with patch.object(ocr, "check_engine"), patch.object(ocr.pytesseract, "run_and_get_output", side_effect=slow_ocr):
            worker = threading.Thread(target=lambda: result.append(self.post(self.png)))
            worker.start()
            try:
                self.assertTrue(started.wait(timeout=5))
                with urlopen(self.base_url + "/api/health", timeout=2) as response:
                    self.assertEqual(response.status, 200)
            finally:
                release.set()
                worker.join(timeout=5)
        self.assertEqual(result[0][0], 200)


class OCRServiceTests(unittest.TestCase):
    def test_invalid_confidences_and_nonword_rows_excluded(self):
        invalid = "".join(
            f"{level}\t1\t1\t1\t1\t1\t1\t1\t4\t3\t{confidence}\tIgnored\n"
            for level, confidence in [(4, 95), (5, -1), (5, "nan"), (5, "bad"), (5, 101)]
        )
        text, tokens = ocr._parse_observations(TSV + invalid, 16, 12)
        self.assertEqual(text, "NEXUS DEMO\n2026")
        self.assertEqual(len(tokens), 3)

    def test_malformed_output_is_failure_not_no_text(self):
        with self.assertRaises(ocr.OCRError) as error:
            ocr._parse_observations("", 16, 12)
        self.assertEqual(error.exception.code, "ocr_failed")

    def test_exif_orientation_and_rgb(self):
        output = BytesIO()
        with Image.new("L", (20, 10), "white") as image:
            exif = image.getexif()
            exif[274] = 6
            image.save(output, format="JPEG", exif=exif)

        def inspect_image(image, **kwargs):
            self.assertEqual(image.mode, "RGB")
            self.assertEqual(image.size, (10, 20))
            return HEADER

        with patch.object(ocr, "check_engine"), patch.object(ocr.pytesseract, "run_and_get_output", side_effect=inspect_image):
            result = ocr.extract_image_clues(output.getvalue(), TextContext())
        self.assertEqual((result.processed_image.width, result.processed_image.height), (10, 20))

    def test_preflight_timeout(self):
        with patch.object(ocr.shutil, "which", return_value="tesseract"), patch.object(ocr.subprocess, "run", side_effect=subprocess.TimeoutExpired("tesseract", 15)):
            with self.assertRaises(ocr.OCRError) as error:
                ocr.check_engine()
        self.assertEqual(error.exception.code, "ocr_timeout")

    def test_wrapper_temporary_files_cleaned_on_success_and_failure(self):
        for failure in (None, RuntimeError("Tesseract process timeout"), pytesseract.TesseractError(1, "failure")):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                def native_call(**kwargs):
                    self.assertTrue(Path(kwargs["input_filename"]).exists())
                    Path(kwargs["output_filename_base"] + ".tsv").write_text(HEADER, encoding="utf-8")
                    if failure:
                        raise failure

                with patch.object(ocr, "check_engine"), patch.object(
                    pytesseract.pytesseract, "NamedTemporaryFile", partial(tempfile.NamedTemporaryFile, dir=directory)
                ), patch.object(pytesseract.pytesseract, "run_tesseract", side_effect=native_call):
                    if failure:
                        with self.assertRaises(ocr.OCRError):
                            ocr.extract_image_clues(inputs.generated_image(), TextContext())
                    else:
                        ocr.extract_image_clues(inputs.generated_image(), TextContext())
                self.assertEqual(list(Path(directory).iterdir()), [])


class RealOCRIntegrationTests(unittest.TestCase):
    """No OCR mocks: explicitly skipped if native Tesseract/English is unavailable."""

    def setUp(self):
        try:
            ocr.check_engine()
        except ocr.OCRError as error:
            if error.code in {"ocr_engine_missing", "ocr_language_missing"}:
                self.skipTest(f"REAL OCR BLOCKED: {error}")
            raise

    def test_real_generated_text(self):
        content = demo_image()
        inputs.validate_image(content)
        result = ocr.extract_image_clues(content, TextContext(name="Supplied Only"))
        self.assertEqual(result.ocr_status, "completed")
        words = result.extracted_text.upper().split()
        for word in ("NEXUS", "DEMO", "2026"):
            self.assertIn(word, words)
        self.assertEqual(result.supplied_context.name, "Supplied Only")
        self.assertNotIn("Supplied Only", result.extracted_text)
        self.assertTrue(result.tokens)

    def test_real_blank_image(self):
        content = demo_image(blank=True)
        inputs.validate_image(content)
        result = ocr.extract_image_clues(content, TextContext())
        self.assertEqual(result.ocr_status, "no_text")
        self.assertEqual(result.extracted_text, "")
        self.assertEqual(result.tokens, [])


if __name__ == "__main__":
    unittest.main()
