"""Environment loading tests using only temporary, synthetic configuration."""

import os
from pathlib import Path
import runpy
import shutil
import tempfile
import unittest
from unittest.mock import patch


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        package = self.root / "backend"
        package.mkdir()
        self.initializer = package / "__init__.py"
        shutil.copyfile(Path(__file__).resolve().parents[1] / "backend" / "__init__.py", self.initializer)

    def test_root_file_loads_independently_of_working_directory(self):
        (self.root / ".env").write_text('TAVILY_API_KEY="synthetic-local-value"\nTESSERACT_CMD=synthetic-command\n', encoding="utf-8")
        # The actual working directory is outside the temporary project root.
        with patch.dict(os.environ, {}, clear=True):
            runpy.run_path(str(self.initializer))
            self.assertEqual(os.environ["TAVILY_API_KEY"], "synthetic-local-value")
            self.assertEqual(os.environ["TESSERACT_CMD"], "synthetic-command")

    def test_os_variables_take_priority_including_empty_values(self):
        (self.root / ".env").write_text("TAVILY_API_KEY=synthetic-local-value\n", encoding="utf-8")
        for value in ("synthetic-os-value", ""):
            with self.subTest(value=value), patch.dict(os.environ, {"TAVILY_API_KEY": value}, clear=True):
                runpy.run_path(str(self.initializer))
                self.assertEqual(os.environ["TAVILY_API_KEY"], value)

    def test_missing_env_file_is_optional(self):
        with patch.dict(os.environ, {}, clear=True):
            runpy.run_path(str(self.initializer))
            self.assertNotIn("TAVILY_API_KEY", os.environ)
