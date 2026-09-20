"""FastAPI entry point for health, temporary input validation, and local OCR."""

from typing import Annotated
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.models import ImageCluesResponse, ImageMetadata, InputResponse, TextContext
from backend.services.image_clues import OCRError, extract_image_clues
from backend.models import SearchPlanRequest, SearchPlanResponse
from backend.services.query_builder import build_search_plan
from backend.models import DiscoveryRequest, DiscoveryResponse
from backend.models import SourceAnalysisRequest, SourceAnalysisResponse, CorrelationRequest, CorrelationResponse
from backend.services.discovery import DiscoveryError, discover
from backend.services.extraction import analyze_candidates
from backend.services.correlation import correlate
from backend.services.input_validation import (
    InputValidationError,
    normalize_context,
    read_image_bytes,
    validate_consent,
    validate_image,
)

app = FastAPI(title="Temporal Nexus")
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.post("/api/correlate", response_model=CorrelationResponse)
def correlate_identity(request: CorrelationRequest) -> CorrelationResponse:
    """Compare analyzed source observations with supplied/reviewed seed identity signals."""
    return correlate(request)


@app.post("/api/analyze-sources", response_model=SourceAnalysisResponse)
def analyze_selected_sources(request: SourceAnalysisRequest) -> SourceAnalysisResponse:
    """Read explicitly selected public candidates and return source observations only."""
    return analyze_candidates(request)


@app.post("/api/discover", response_model=DiscoveryResponse)
def discover_sources(request: DiscoveryRequest):
    """Send prepared query text to Tavily; never retrieve result pages."""
    try:
        result, status_code = discover(request)
        return JSONResponse(result.model_dump(mode="json"), status_code=status_code)
    except DiscoveryError as error:
        raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": str(error)}) from None


@app.post("/api/search-plan", response_model=SearchPlanResponse)
def search_plan(request: SearchPlanRequest) -> SearchPlanResponse:
    """Prepare local query strings only; no search or external request occurs."""
    return build_search_plan(request)


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    """Serve the test interface; only frontend assets are exposed."""
    return FileResponse(FRONTEND_DIR / "index.html")


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
    role: Annotated[str | None, Form()] = None,
    department: Annotated[str | None, Form()] = None,
) -> tuple[bytes, TextContext, ImageMetadata]:
    """Shared validation dependency; always close the temporary upload."""
    try:
        validate_consent(consent_confirmed)
        context = normalize_context(name, username, organization, role, department)
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
