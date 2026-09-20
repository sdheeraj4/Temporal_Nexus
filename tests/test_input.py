"""Focused validation tests using generated non-personal images and a local server."""

import asyncio
from io import BytesIO
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from PIL import Image, PngImagePlugin
import uvicorn

from backend.main import app
from backend.services.input_validation import (
    InputValidationError,
    MAX_IMAGE_BYTES,
    READ_CHUNK_BYTES,
    read_image_bytes,
    validate_image,
)


def generated_image(format="PNG", size=(16, 12)):
    """Generate a solid-color image entirely in memory."""
    output = BytesIO()
    with Image.new("RGB", size, (32, 64, 128)) as image:
        image.save(output, format=format)
    return output.getvalue()


class InputEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.png = generated_image()
        cls.jpeg = generated_image("JPEG")
        cls.socket = socket.socket()
        cls.socket.bind(("127.0.0.1", 0))
        cls.base_url = f"http://127.0.0.1:{cls.socket.getsockname()[1]}"
        cls.server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        cls.thread = threading.Thread(
            target=cls.server.run, kwargs={"sockets": [cls.socket]}, daemon=True
        )
        cls.thread.start()
        for _ in range(100):
            if cls.server.started:
                return
            if not cls.thread.is_alive():
                break
            time.sleep(0.05)
        cls.server.should_exit = True
        cls.thread.join(timeout=5)
        cls.socket.close()
        raise RuntimeError("Test server failed to start")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        cls.socket.close()
        if cls.thread.is_alive():
            raise RuntimeError("Test server failed to stop")

    def post(self, image, fields=None, filename="test.png", mime="image/png", path="/api/input"):
        boundary = uuid4().hex
        parts = []
        for key, value in (fields if fields is not None else {"consent_confirmed": "true"}).items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
            )
        if image is not None:
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\n'
                f'Content-Type: {mime}\r\n\r\n'.encode() + image + b"\r\n"
            )
        parts.append(f"--{boundary}--\r\n".encode())
        request = Request(
            self.base_url + path,
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            response = urlopen(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_valid_png_and_normalized_context(self):
        status, body = self.post(self.png, {
            "consent_confirmed": "true", "name": "  Test  ",
            "username": "  demo  ", "organization": "   ",
        })
        self.assertEqual(status, 200)
        self.assertEqual(body, {
            "status": "validated",
            "context": {"name": "Test", "username": "demo", "organization": None, "role": None, "department": None},
            "image": {"format": "PNG", "width": 16, "height": 12, "byte_size": len(self.png)},
            "message": "Input validated. OCR and identity correlation have not run.",
        })

    def test_valid_jpeg(self):
        status, body = self.post(self.jpeg, filename="test.jpg", mime="image/jpeg")
        self.assertEqual(status, 200)
        self.assertEqual(body["image"], {
            "format": "JPEG", "width": 16, "height": 12, "byte_size": len(self.jpeg)
        })
        self.assertEqual(body["context"], {"name": None, "username": None, "organization": None, "role": None, "department": None})

    def test_actual_content_overrides_filename_and_mime(self):
        status, body = self.post(self.png, filename="misleading.txt", mime="text/plain")
        self.assertEqual(status, 200)
        self.assertEqual(body["image"]["format"], "PNG")

    def test_missing_image(self):
        status, body = self.post(None)
        self.assertEqual(status, 422)
        self.assertIn("image", body["detail"][0]["loc"])

    def test_empty_image(self):
        status, body = self.post(b"")
        self.assertEqual(status, 400)
        self.assertIn("empty", body["detail"])

    def test_disguised_text(self):
        status, body = self.post(b"This is plain text, not an image.")
        self.assertEqual(status, 415)
        self.assertIn("valid JPEG or PNG", body["detail"])

    def test_unsupported_image(self):
        status, body = self.post(generated_image("GIF"))
        self.assertEqual(status, 415)
        self.assertIn("Unsupported", body["detail"])

    def test_corrupt_images(self):
        for data in (self.png[:-15], self.jpeg[:-20]):
            with self.subTest(signature=data[:8]):
                status, body = self.post(data)
                self.assertEqual(status, 400)
                self.assertIn("corrupt or incomplete", body["detail"])

    def test_exceeds_byte_limit(self):
        status, body = self.post(self.png + b"\0" * (MAX_IMAGE_BYTES + 1 - len(self.png)))
        self.assertEqual(status, 413)
        self.assertIn("5 MiB", body["detail"])

    def test_exact_byte_limit(self):
        status, body = self.post(self.png + b"\0" * (MAX_IMAGE_BYTES - len(self.png)))
        self.assertEqual(status, 200)
        self.assertEqual(body["image"]["byte_size"], MAX_IMAGE_BYTES)

    def test_exceeds_pixel_limit(self):
        status, body = self.post(generated_image(size=(5000, 4001)))
        self.assertEqual(status, 413)
        self.assertIn("20 megapixels", body["detail"])

    def test_exact_pixel_limit(self):
        status, body = self.post(generated_image(size=(5000, 4000)))
        self.assertEqual(status, 200)
        self.assertEqual(body["image"]["width"] * body["image"]["height"], 20_000_000)

    def test_missing_consent(self):
        status, body = self.post(self.png, {})
        self.assertEqual(status, 422)
        self.assertIn("consent_confirmed", body["detail"][0]["loc"])

    def test_false_consent(self):
        status, body = self.post(self.png, {"consent_confirmed": "false"})
        self.assertEqual(status, 422)
        self.assertEqual(body["detail"], "consent_confirmed must be true.")

    def test_invalid_boolean(self):
        status, _ = self.post(self.png, {"consent_confirmed": "not-a-boolean"})
        self.assertEqual(status, 422)

    def test_overlong_text(self):
        for field in ("name", "username", "organization", "role", "department"):
            with self.subTest(field=field):
                status, body = self.post(self.png, {"consent_confirmed": "true", field: "x" * 201})
                self.assertEqual(status, 422)
                self.assertIn(field, body["detail"])
                self.assertIn("200 characters", body["detail"])
                self.assertNotIn("x" * 201, json.dumps(body))

    def test_exact_text_limit_after_trimming(self):
        fields = {field: "  " + "x" * 200 + "  " for field in ("name", "username", "organization", "role", "department")}
        status, body = self.post(self.png, {"consent_confirmed": "true", **fields})
        self.assertEqual(status, 200)
        self.assertTrue(all(len(value) == 200 for value in body["context"].values()))

    def test_health_and_docs_preserved(self):
        with urlopen(self.base_url + "/api/health", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.load(response), {"status": "ok", "project": "Temporal Nexus"})
        with urlopen(self.base_url + "/docs", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"SwaggerUIBundle", response.read())

    def test_schema_identifies_supplied_context(self):
        with urlopen(self.base_url + "/openapi.json", timeout=5) as response:
            self.assertEqual(response.status, 200)
            schema = json.load(response)
        description = schema["components"]["schemas"]["InputResponse"]["properties"]["context"]["description"]
        self.assertIn("user-supplied context", description)
        self.assertIn("not OCR observations", description)


class ValidationLimitTests(unittest.TestCase):
    def test_pixel_limit_checked_before_full_decode(self):
        content = generated_image(size=(5000, 4001))
        with patch.object(PngImagePlugin.PngImageFile, "load", side_effect=AssertionError("Decoded too soon")) as load:
            with self.assertRaises(InputValidationError) as error:
                validate_image(content)
            self.assertEqual(error.exception.status_code, 413)
            load.assert_not_called()

    def test_upload_reads_are_bounded(self):
        class EndlessUpload:
            total = 0

            async def read(self, size=-1):
                if not 0 < size <= READ_CHUNK_BYTES:
                    raise AssertionError(f"Unbounded read: {size}")
                self.total += size
                return b"x" * size

        upload = EndlessUpload()
        with self.assertRaises(InputValidationError) as error:
            asyncio.run(read_image_bytes(upload))
        self.assertEqual(error.exception.status_code, 413)
        self.assertEqual(upload.total, MAX_IMAGE_BYTES + 1)


if __name__ == "__main__":
    unittest.main()
