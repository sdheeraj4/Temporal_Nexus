"""Local, bounded English scene-text OCR of validated images; no identity inference or storage."""

import csv
from difflib import SequenceMatcher
from io import BytesIO, StringIO
import math
import os
import re
import shutil
import subprocess
import time

from PIL import Image, ImageOps
import pytesseract

from backend.models import (
    ImageCluesResponse, OCRBoundingBox, OCRToken, ProcessedImage, TextContext,
)

OCR_TIMEOUT_SECONDS = 15
MAX_OCR_SIDE = 1800
MIN_PASS_BUDGET_SECONDS = 0.75
MIN_LINE_CONFIDENCE = 55.0
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


def _parse_line_groups(tsv: str, width: int, height: int) -> list[list[OCRToken]]:
    """Parse valid Tesseract word rows and preserve their line grouping/read order."""
    reader = csv.DictReader(StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    required = {"level", "page_num", "block_num", "par_num", "line_num", "left", "top", "width", "height", "conf", "text"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise OCRError("ocr_failed", "Local OCR returned an invalid result.", 500)
    lines: list[list[OCRToken]] = []
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
        lines[-1].append(OCRToken(
            text=text, ocr_confidence=confidence,
            bounding_box=OCRBoundingBox(left=left, top=top, width=box_width, height=box_height),
        ))
    return [line for line in lines if line]


def _parse_observations(tsv: str, width: int, height: int) -> tuple[str, list[OCRToken]]:
    """Preserve Tesseract reading order and line breaks; discard invalid word entries."""
    lines = _parse_line_groups(tsv, width, height)
    return "\n".join(" ".join(token.text for token in line) for line in lines), [token for line in lines for token in line]


def _fit_for_ocr(image: Image.Image) -> tuple[Image.Image, float, float]:
    """Bound work on large images while retaining enough pixels for scene text."""
    width, height = image.size
    longest = max(width, height)
    if longest <= MAX_OCR_SIDE:
        return image.copy(), 1.0, 1.0
    scale = MAX_OCR_SIDE / longest
    resized = image.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS)
    return resized, width / resized.width, height / resized.height


def _enhance_scene_text(image: Image.Image) -> Image.Image:
    """Create a conservative high-contrast grayscale view without destroying large signage."""
    return ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)


def _pass_specs(image: Image.Image):
    """Yield bounded OCR views: sparse full-scene passes plus overlapping horizontal bands."""
    width, height = image.size
    # Tiny images are primarily used for unit/API contract checks; one pass is sufficient.
    if width < 320 or height < 160:
        yield image, (0, 0), "--psm 11 -c preserve_interword_spaces=1"
        return

    enhanced = _enhance_scene_text(image)
    yield image, (0, 0), "--psm 11 -c preserve_interword_spaces=1"
    yield enhanced, (0, 0), "--psm 11 -c preserve_interword_spaces=1"

    # Scene photographs often contain one sign/banner occupying only part of the frame.
    # Overlapping bands reduce background clutter while still covering the entire image.
    band_height = max(160, round(height * 0.55))
    starts = [0, max(0, round(height * 0.225)), max(0, height - band_height)]
    seen = set()
    for top in starts:
        bottom = min(height, top + band_height)
        key = (top, bottom)
        if key in seen or bottom - top < 80:
            continue
        seen.add(key)
        yield enhanced.crop((0, top, width, bottom)), (0, top), "--psm 6"



def _split_sparse_group(group: list[OCRToken]) -> list[list[OCRToken]]:
    """Split Tesseract lines when distant scene-text regions were incorrectly joined."""
    if len(group) < 2:
        return [group]
    ordered = sorted(group, key=lambda token: token.bounding_box.left)
    heights = sorted(token.bounding_box.height for token in ordered)
    median_height = heights[(len(heights) - 1) // 2]
    gap_limit = max(80, round(median_height * 1.2))
    chunks: list[list[OCRToken]] = [[ordered[0]]]
    for token in ordered[1:]:
        previous = chunks[-1][-1]
        previous_right = previous.bounding_box.left + previous.bounding_box.width
        if token.bounding_box.left - previous_right > gap_limit:
            chunks.append([token])
        else:
            chunks[-1].append(token)
    return chunks

def _map_token(token: OCRToken, offset: tuple[int, int], scale_x: float, scale_y: float,
               original_width: int, original_height: int) -> OCRToken:
    """Map a crop/resized-pass token back into the EXIF-corrected original coordinate space."""
    box = token.bounding_box
    left = max(0, min(original_width - 1, round((offset[0] + box.left) * scale_x)))
    top = max(0, min(original_height - 1, round((offset[1] + box.top) * scale_y)))
    right = max(left + 1, min(original_width, round((offset[0] + box.left + box.width) * scale_x)))
    bottom = max(top + 1, min(original_height, round((offset[1] + box.top + box.height) * scale_y)))
    return OCRToken(
        text=token.text,
        ocr_confidence=token.ocr_confidence,
        bounding_box=OCRBoundingBox(left=left, top=top, width=right - left, height=bottom - top),
    )


def _line_key(text: str) -> str:
    return "".join(character.casefold() for character in text if character.isalnum())


def _meaningful_line(text: str, confidence: float) -> bool:
    alnum = [character for character in text if character.isalnum()]
    if len(alnum) < 2 or confidence < MIN_LINE_CONFIDENCE:
        return False
    visible = [character for character in text if not character.isspace()]
    return bool(visible) and len(alnum) / len(visible) >= 0.45


def _same_line(a: str, b: str) -> bool:
    """Conservatively identify repeated/partial readings produced by multiple OCR passes."""
    key_a, key_b = _line_key(a), _line_key(b)
    if not key_a or not key_b:
        return False
    if key_a == key_b:
        return True
    shorter, longer = sorted((key_a, key_b), key=len)
    if len(shorter) >= 5 and shorter in longer and len(shorter) / len(longer) >= 0.30:
        return True
    return min(len(key_a), len(key_b)) >= 6 and SequenceMatcher(None, key_a, key_b).ratio() >= 0.88


def _merge_lines(candidates: list[dict]) -> list[dict]:
    """Collapse repeated/partial readings, preferring complete clean text at the same scene region."""

    def bbox(item: dict) -> tuple[int, int, int, int]:
        tokens = item.get("tokens") or []
        left = min(token.bounding_box.left for token in tokens)
        top = min(token.bounding_box.top for token in tokens)
        right = max(token.bounding_box.left + token.bounding_box.width for token in tokens)
        bottom = max(token.bounding_box.top + token.bounding_box.height for token in tokens)
        return left, top, right, bottom

    def short_spatial_match(a: dict, b: dict) -> bool:
        key_a, key_b = _line_key(a["text"]), _line_key(b["text"])
        if not (2 <= len(key_a) <= 5 and 2 <= len(key_b) <= 5):
            return False
        if SequenceMatcher(None, key_a, key_b).ratio() < 0.55:
            return False
        ax1, ay1, ax2, ay2 = bbox(a)
        bx1, by1, bx2, by2 = bbox(b)
        vertical_overlap = max(0, min(ay2, by2) - max(ay1, by1))
        min_height = max(1, min(ay2 - ay1, by2 - by1))
        horizontal_overlap = max(0, min(ax2, bx2) - max(ax1, bx1))
        min_width = max(1, min(ax2 - ax1, bx2 - bx1))
        return vertical_overlap / min_height >= 0.5 and horizontal_overlap / min_width >= 0.5

    def quality(item: dict) -> float:
        text = item["text"]
        words = [word for word in re.findall(r"[A-Za-z]+", text) if word]
        symbols = sum(1 for char in text if not char.isalnum() and not char.isspace())
        uppercase_bonus = 3.0 if len(words) >= 2 and all(word.isupper() for word in words) else 0.0
        mixed_penalty = sum(1.5 for word in words if len(word) >= 4 and not (word.islower() or word.isupper() or word.istitle()))
        return item["confidence"] + min(len(_line_key(text)), 30) * 0.35 + uppercase_bonus - symbols * 4.0 - mixed_penalty

    # Complete strings first so partial readings such as "AMPUS" collapse into "TECHNICAL CAMPUS".
    ordered = sorted(candidates, key=lambda item: (-len(_line_key(item["text"])), -quality(item)))
    merged: list[dict] = []
    for candidate in ordered:
        match = next((item for item in merged if _same_line(item["text"], candidate["text"]) or short_spatial_match(item, candidate)), None)
        if match is None:
            merged.append(candidate)
            continue
        if quality(candidate) > quality(match) + 1.5:
            match.update(candidate)

    # Restore approximate visual reading order after de-duplication.
    merged.sort(key=lambda item: (bbox(item)[1], bbox(item)[0]))
    return merged


def _merge_tokens(tokens: list[OCRToken]) -> list[OCRToken]:
    """Deduplicate near-identical words from overlapping OCR passes."""
    merged: list[OCRToken] = []
    for token in tokens:
        key = _line_key(token.text)
        if not key:
            continue
        box = token.bounding_box
        duplicate = None
        for existing in merged:
            if _line_key(existing.text) != key:
                continue
            other = existing.bounding_box
            tolerance = max(8, min(box.height, other.height))
            if abs(box.left - other.left) <= tolerance and abs(box.top - other.top) <= tolerance:
                duplicate = existing
                break
        if duplicate is None:
            merged.append(token)
        elif token.ocr_confidence > duplicate.ocr_confidence:
            merged[merged.index(duplicate)] = token
    return merged


def _run_scene_ocr(processed: Image.Image, original_width: int, original_height: int,
                   scale_x: float, scale_y: float, deadline: float) -> tuple[str, list[OCRToken]]:
    """Run several bounded Tesseract scene-text passes and merge their strongest observations."""
    line_candidates: list[dict] = []
    all_tokens: list[OCRToken] = []
    pass_count = 0

    for view, offset, config in _pass_specs(processed):
        remaining = _remaining(deadline)
        if pass_count and remaining < MIN_PASS_BUDGET_SECONDS:
            break
        try:
            tsv = pytesseract.run_and_get_output(
                view, extension="tsv", lang="eng",
                config=f"-c tessedit_create_tsv=1 {config}", timeout=remaining,
            )
        except RuntimeError as exc:
            # If a later enhancement pass exhausts the shared budget, preserve earlier valid OCR.
            if pass_count and "timeout" in str(exc).lower() and line_candidates:
                break
            raise
        pass_count += 1
        groups = _parse_line_groups(tsv, view.width, view.height)
        for raw_group in groups:
            for group in _split_sparse_group(raw_group):
                mapped = [_map_token(token, offset, scale_x, scale_y, original_width, original_height) for token in group]
                text = " ".join(token.text for token in group).strip()
                confidence = sum(token.ocr_confidence for token in group) / len(group)
                if not _meaningful_line(text, confidence):
                    continue
                line_candidates.append({"text": text, "confidence": confidence, "tokens": mapped})
                all_tokens.extend(mapped)

    merged_lines = _merge_lines(line_candidates)
    text = "\n".join(item["text"] for item in merged_lines)
    return text, _merge_tokens(all_tokens)


def extract_image_clues(content: bytes, supplied_context: TextContext) -> ImageCluesResponse:
    """Run multi-pass scene-text OCR on already-validated bytes in a worker thread."""
    deadline = time.monotonic() + OCR_TIMEOUT_SECONDS
    check_engine(_remaining(deadline))
    try:
        with Image.open(BytesIO(content)) as original:
            with ImageOps.exif_transpose(original) as oriented:
                with oriented.convert("RGB") as rgb:
                    width, height = rgb.size
                    processed, scale_x, scale_y = _fit_for_ocr(rgb)
                    extracted_text, tokens = _run_scene_ocr(
                        processed, width, height, scale_x, scale_y, deadline
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

    return ImageCluesResponse(
        supplied_context=supplied_context, ocr_status="completed" if tokens else "no_text",
        extracted_text=extracted_text, tokens=tokens,
        processed_image=ProcessedImage(width=width, height=height),
        message=(
            "Multi-pass local OCR produced unverified scene-text observations. Recognition confidence is not "
            "identity confidence. Text does not establish identity, attendance, or employment. Review, correct, "
            "or add a missed visible clue before discovery; identity correlation has not run."
        ),
    )
