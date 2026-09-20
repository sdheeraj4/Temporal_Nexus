"""Backend package for Temporal Nexus."""

from pathlib import Path

from dotenv import load_dotenv

# Load before services read configuration, independently of the working directory.
# Existing OS variables (including explicitly empty values) take precedence.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
