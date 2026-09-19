"""Validate temporary image uploads and normalize optional text context."""

from io import BytesIO

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from backend.models import ImageMetadata, TextContext

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_TEXT_LENGTH = 200
READ_CHUNK_BYTES = 64 * 1024


class InputValidationError(ValueError):
    """A safe client-facing error that contains no submitted personal data."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def validate_consent(consent_confirmed: bool) -> None:
    """Require explicit affirmative consent before image validation."""
    if consent_confirmed is not True:
        raise InputValidationError("consent_confirmed must be true.")


def normalize_context(
    name: str | None, username: str | None, organization: str | None
) -> TextContext:
    """Trim each optional field and enforce the normalized character limit."""
    values = {}
    for field, value in (
        ("name", name), ("username", username), ("organization", organization)
    ):
        normalized = value.strip() if value is not None else None
        if normalized and len(normalized) > MAX_TEXT_LENGTH:
            raise InputValidationError(f"{field} must be at most 200 characters after trimming.")
        values[field] = normalized or None
    return TextContext(**values)


async def read_image_bytes(upload: UploadFile) -> bytes:
    """Read at most 5 MiB plus one sentinel byte, in bounded chunks."""
    content = bytearray()
    while len(content) <= MAX_IMAGE_BYTES:
        chunk = await upload.read(min(READ_CHUNK_BYTES, MAX_IMAGE_BYTES + 1 - len(content)))
        if not chunk:
            break
        content.extend(chunk)
    if len(content) > MAX_IMAGE_BYTES:
        raise InputValidationError("Image file must not exceed 5 MiB (5242880 bytes).", 413)
    if not content:
        raise InputValidationError("Image file must not be empty.", 400)
    return bytes(content)


def validate_image(content: bytes) -> ImageMetadata:
    """Identify actual content, check dimensions before decoding, and check integrity."""
    if not content:
        raise InputValidationError("Image file must not be empty.", 400)
    if len(content) > MAX_IMAGE_BYTES:
        raise InputValidationError("Image file must not exceed 5 MiB (5242880 bytes).", 413)
    try:
        # Restrict decoders, ignoring both client MIME type and filename.
        with Image.open(BytesIO(content), formats=("JPEG", "PNG")) as image:
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise InputValidationError("Image must not exceed 20 megapixels (20000000 pixels).", 413)
            detected_format = image.format
            image.verify()
        # verify() checks structure; load() also detects truncated pixel data.
        with Image.open(BytesIO(content), formats=("JPEG", "PNG")) as image:
            image.load()
    except InputValidationError:
        raise
    except Image.DecompressionBombError:
        raise InputValidationError("Image must not exceed 20 megapixels (20000000 pixels).", 413) from None
    except UnidentifiedImageError:
        raise InputValidationError("Unsupported or unrecognized image content; provide a valid JPEG or PNG.", 415) from None
    except (OSError, SyntaxError, ValueError, EOFError):
        raise InputValidationError("Image is corrupt or incomplete; provide a valid JPEG or PNG.", 400) from None
    return ImageMetadata(format=detected_format, width=width, height=height, byte_size=len(content))
