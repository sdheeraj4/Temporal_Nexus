"""FastAPI entry point for health, temporary input validation, and local OCR."""

from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from backend.models import ImageCluesResponse, ImageMetadata, InputResponse, TextContext
from backend.services.image_clues import OCRError, extract_image_clues
from backend.services.input_validation import (
    InputValidationError,
    normalize_context,
    read_image_bytes,
    validate_consent,
    validate_image,
)

app = FastAPI(title="Temporal Nexus")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Report server availability only, not integration readiness."""
    return {"status": "ok", "project": "Temporal Nexus"}


async def validated_upload(
    image: Annotated[UploadFile, File(description="JPEG or PNG, up to 5 MiB and 20 megapixels")],
    consent_confirmed: Annotated[bool, Form()],
    name: Annotated[str | None, Form()] = None,
    username: Annotated[str | None, Form()] = None,
    organization: Annotated[str | None, Form()] = None,
) -> tuple[bytes, TextContext, ImageMetadata]:
    """Shared validation dependency; always close the temporary upload."""
    try:
        validate_consent(consent_confirmed)
        context = normalize_context(name, username, organization)
        content = await read_image_bytes(image)
        metadata = await run_in_threadpool(validate_image, content)
        return content, context, metadata
    except InputValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
    finally:
        await image.close()


@app.post("/api/input", response_model=InputResponse)
async def validate_input(
    validated: Annotated[tuple[bytes, TextContext, ImageMetadata], Depends(validated_upload)],
) -> InputResponse:
    """Validate an upload and context without analysis or persistent storage."""
    _, context, metadata = validated
    return InputResponse(context=context, image=metadata)


@app.post("/api/image-clues", response_model=ImageCluesResponse)
async def image_clues(
    validated: Annotated[tuple[bytes, TextContext, ImageMetadata], Depends(validated_upload)],
) -> ImageCluesResponse:
    """Return local OCR observations only after all input validation succeeds."""
    content, context, _ = validated
    try:
        return await run_in_threadpool(extract_image_clues, content, context)
    except OCRError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}
        ) from None
