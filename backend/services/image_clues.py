"""Local, bounded English OCR of validated images; no identity inference or storage."""

import csv
from io import BytesIO, StringIO
import math
import os
import shutil
import subprocess
import time

from PIL import Image, ImageOps
import pytesseract

from backend.models import (
    ImageCluesResponse, OCRBoundingBox, OCRToken, ProcessedImage, TextContext,
)

OCR_TIMEOUT_SECONDS = 15
# Configure once at startup, avoiding per-request mutations of wrapper state.
pytesseract.pytesseract.tesseract_cmd = os.environ.get("TESSERACT_CMD", "").strip() or "tesseract"


class OCRError(RuntimeError):
    """Safe OCR failure details, excluding native stderr and personal data."""

    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OCRError("ocr_timeout", "Local OCR exceeded the 15-second timeout.", 504)
    return remaining


def check_engine(timeout: float = OCR_TIMEOUT_SECONDS) -> None:
    """Check executable and English data with a bounded native command."""
    executable = pytesseract.pytesseract.tesseract_cmd
    if not shutil.which(executable):
        raise OCRError("ocr_engine_missing", "Install local Tesseract and set PATH or TESSERACT_CMD.", 503)
    try:
        result = subprocess.run(
            [executable, "--list-langs"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError:
        raise OCRError("ocr_engine_missing", "Install local Tesseract and set PATH or TESSERACT_CMD.", 503) from None
    except subprocess.TimeoutExpired:
        raise OCRError("ocr_timeout", "Local OCR exceeded the 15-second timeout.", 504) from None
    except OSError:
        raise OCRError("ocr_unavailable", "The local Tesseract executable could not be started.", 503) from None
    if result.returncode != 0:
        raise OCRError("ocr_unavailable", "Tesseract could not inspect its language data; check the installation.", 503)
    if "eng" not in {line.strip() for line in result.stdout.splitlines()}:
        raise OCRError("ocr_language_missing", "Tesseract English language data (eng.traineddata) is missing.", 503)


def _parse_observations(tsv: str, width: int, height: int) -> tuple[str, list[OCRToken]]:
    """Preserve Tesseract reading order and line breaks; discard invalid word entries."""
    reader = csv.DictReader(StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    required = {"level", "page_num", "block_num", "par_num", "line_num", "left", "top", "width", "height", "conf", "text"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise OCRError("ocr_failed", "Local OCR returned an invalid result.", 500)
    tokens = []
    lines = []
    previous_line = None
    for row in reader:
        text = (row.get("text") or "").strip()
        try:
            confidence = float(row["conf"])
            if row["level"] != "5" or not text or not math.isfinite(confidence) or not 0 <= confidence <= 100:
                continue
            left, top, box_width, box_height = (int(row[key]) for key in ("left", "top", "width", "height"))
            if left < 0 or top < 0 or box_width <= 0 or box_height <= 0 or left + box_width > width or top + box_height > height:
                continue
        except (TypeError, ValueError):
            continue
        line = tuple(row[key] for key in ("page_num", "block_num", "par_num", "line_num"))
        if line != previous_line:
            lines.append([])
            previous_line = line
        lines[-1].append(text)
        tokens.append(OCRToken(
            text=text, ocr_confidence=confidence,
            bounding_box=OCRBoundingBox(left=left, top=top, width=box_width, height=box_height),
        ))
    return "\n".join(" ".join(line) for line in lines), tokens


def extract_image_clues(content: bytes, supplied_context: TextContext) -> ImageCluesResponse:
    """OCR already-validated bytes in a worker thread; wrapper cleans temporary files."""
    deadline = time.monotonic() + OCR_TIMEOUT_SECONDS
    check_engine(_remaining(deadline))
    try:
        with Image.open(BytesIO(content)) as original:
            with ImageOps.exif_transpose(original) as oriented:
                with oriented.convert("RGB") as processed:
                    width, height = processed.size
                    # This wrapper API avoids image_to_data's unbounded version probe.
                    # Its save() context cleans input/output files even on timeout/failure.
                    tsv = pytesseract.run_and_get_output(
                        processed, extension="tsv", lang="eng",
                        config="-c tessedit_create_tsv=1", timeout=_remaining(deadline),
                    )
    except pytesseract.TesseractNotFoundError:
        raise OCRError("ocr_engine_missing", "Install local Tesseract and set PATH or TESSERACT_CMD.", 503) from None
    except pytesseract.TesseractError:
        raise OCRError("ocr_failed", "Local OCR failed; check Tesseract and its English language data.", 500) from None
    except OCRError:
        raise
    except RuntimeError as exc:
        if "timeout" in str(exc).lower():
            raise OCRError("ocr_timeout", "Local OCR exceeded the 15-second timeout.", 504) from None
        raise OCRError("ocr_failed", "Local OCR failed to process the image.", 500) from None
    except (OSError, ValueError):
        raise OCRError("ocr_failed", "Local OCR failed to process the image.", 500) from None
    extracted_text, tokens = _parse_observations(tsv, width, height)
    return ImageCluesResponse(
        supplied_context=supplied_context, ocr_status="completed" if tokens else "no_text",
        extracted_text=extracted_text, tokens=tokens,
        processed_image=ProcessedImage(width=width, height=height),
    )
