"""HTTP checks for the frontend and the boundary of exposed static files."""

import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

import test_input as inputs


class FrontendHTTPTests(unittest.TestCase):
    setUpClass = classmethod(inputs.InputEndpointTests.setUpClass.__func__)
    tearDownClass = classmethod(inputs.InputEndpointTests.tearDownClass.__func__)

    def test_page_and_assets(self):
        for path, content_type, expected in (
            ("/", "text/html", b"Temporal Nexus"),
            ("/static/styles.css", "text/css", b".workspace"),
            ("/static/app.js", "javascript", b"/api/input"),
        ):
            with self.subTest(path=path), urlopen(self.base_url + path, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(content_type, response.headers["Content-Type"])
                self.assertIn(expected, response.read())

    def test_repository_and_data_are_not_exposed(self):
        for path in (
            "/.env.example", "/README.md", "/backend/main.py", "/data/.gitkeep",
            "/static/.env.example", "/static/data/.gitkeep", "/static/%2e%2e/.env.example",
            "/static/%2e%2e/backend/main.py",
        ):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                urlopen(self.base_url + path, timeout=5)
            self.assertEqual(error.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
