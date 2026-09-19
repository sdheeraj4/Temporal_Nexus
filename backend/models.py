"""Shared data structures for claims, sources, and supporting evidence."""

from typing import Literal

from pydantic import BaseModel, Field


class TextContext(BaseModel):
    """User-supplied context only, not OCR observations; blank values become null."""

    name: str | None = None
    username: str | None = None
    organization: str | None = None


class ImageMetadata(BaseModel):
    """Detected image properties without image contents or filesystem paths."""

    format: Literal["JPEG", "PNG"]
    width: int
    height: int
    byte_size: int


class InputResponse(BaseModel):
    """Validation result; does not indicate that image analysis has run."""

    status: Literal["validated"] = "validated"
    context: TextContext = Field(
        description="Normalized user-supplied context, not OCR observations or verified identity claims."
    )
    image: ImageMetadata
    message: str = "Input validated. OCR and identity correlation have not run."


class OCRBoundingBox(BaseModel):
    """Pixel coordinates measured from the processed image's top-left corner."""

    left: int = Field(ge=0)
    top: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class OCRToken(BaseModel):
    """An unverified word observation with recognition confidence only."""

    text: str
    ocr_confidence: float = Field(
        ge=0, le=100, description="Tesseract OCR recognition confidence (0–100), not identity confidence."
    )
    bounding_box: OCRBoundingBox


class ProcessedImage(BaseModel):
    """Dimensions after EXIF orientation correction and RGB conversion."""

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    coordinate_space: str = "Pixels in the EXIF-corrected RGB image; origin at top-left."


class ImageCluesResponse(BaseModel):
    """Local OCR observations, kept separate from supplied context."""

    supplied_context: TextContext
    ocr_status: Literal["completed", "no_text"]
    extracted_text: str
    tokens: list[OCRToken]
    processed_image: ProcessedImage
    message: str = (
        "OCR text is an unverified observation. Recognition confidence is not identity confidence. "
        "Text does not establish identity, attendance, or employment. Identity correlation has not run."
    )
