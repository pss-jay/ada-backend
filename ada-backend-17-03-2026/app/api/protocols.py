"""Protocol API endpoints for the Ada backend."""

import json
import uuid
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Body
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import settings
from app.services.pdf_parser import extract_text
from app.services.extraction_service import ExtractionService
from app.services.docx_service import DocxService
from app.integrations.docx_gen.animal_detector import AnimalDetectorService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/protocols", tags=["protocols"])

# Services (singleton-ish for Phase 1)
extraction_service = ExtractionService()
docx_service = DocxService()
animal_detector = AnimalDetectorService()


# --- Request/Response Models ---

class ExtractRequest(BaseModel):
    animal_type: str


class UpdateFieldsRequest(BaseModel):
    sections: dict


class GenerateRequest(BaseModel):
    template_override: Optional[str] = None


# --- Helper: JSON file storage (Phase 1, replaces MongoDB) ---

def _save_protocol(protocol_id: str, data: dict):
    """Save protocol data to a JSON file."""
    path = settings.DATA_DIR / f"{protocol_id}.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _load_protocol(protocol_id: str) -> dict:
    """Load protocol data from a JSON file."""
    path = settings.DATA_DIR / f"{protocol_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Protocol {protocol_id} not found")
    with open(path) as f:
        return json.load(f)


# --- Endpoints ---

@router.post("/upload")
async def upload_sow(file: UploadFile = File(...)):
    """Upload a SOW document (PDF or DOCX).

    Returns upload ID, detected species, and extracted text length.
    """
    # Validate file type
    allowed_types = {".pdf", ".docx", ".doc"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Allowed: {', '.join(allowed_types)}"
        )

    # Save uploaded file
    upload_id = str(uuid.uuid4())[:8]
    upload_path = settings.UPLOAD_DIR / f"{upload_id}{ext}"

    try:
        content = await file.read()
        with open(upload_path, "wb") as f:
            f.write(content)
        logger.info(f"Saved upload {upload_id}: {file.filename} ({len(content)} bytes)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

    # Extract text from document
    try:
        document_text = extract_text(str(upload_path))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to extract text: {str(e)}")

    # Detect species from document text + filename
    search_text = f"{file.filename or ''} {document_text[:2000]}"
    species = animal_detector.detect_animal(search_text)

    # Store protocol metadata
    protocol_data = {
        "upload_id": upload_id,
        "filename": file.filename,
        "file_path": str(upload_path),
        "document_text": document_text,
        "species_detected": species,
        "text_length": len(document_text),
        "status": "pending_extraction",
        "extracted_json": None,
    }
    _save_protocol(upload_id, protocol_data)

    return {
        "upload_id": upload_id,
        "filename": file.filename,
        "species_detected": species,
        "text_length": len(document_text),
        "status": "pending_extraction",
    }


@router.post("/{protocol_id}/extract")
async def extract_data(protocol_id: str, req: ExtractRequest):
    """Run AI extraction on an uploaded SOW document.

    Calls Azure OpenAI to extract structured protocol data.
    """
    protocol = _load_protocol(protocol_id)

    if protocol.get("status") not in ("pending_extraction", "extraction_complete", "modified"):
        # Allow re-extraction
        pass

    document_text = protocol.get("document_text", "")
    if not document_text:
        raise HTTPException(status_code=400, detail="No document text available for extraction")

    animal_type = req.animal_type
    if animal_type not in ("rat", "dog", "swine"):
        raise HTTPException(status_code=400, detail=f"Invalid animal type: {animal_type}")

    try:
        result = extraction_service.extract(document_text, animal_type)
    except Exception as e:
        logger.error(f"Extraction failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")

    # Update stored protocol
    protocol["extracted_json"] = result["extracted_json"]
    protocol["species"] = animal_type
    protocol["extraction_time_ms"] = result["extraction_time_ms"]
    protocol["mock_mode"] = result["mock_mode"]
    protocol["status"] = "extraction_complete"
    _save_protocol(protocol_id, protocol)

    return {
        "protocol_id": protocol_id,
        "extracted_json": result["extracted_json"],
        "extraction_time_ms": result["extraction_time_ms"],
        "mock_mode": result["mock_mode"],
        "status": "extraction_complete",
    }


@router.get("/{protocol_id}")
async def get_protocol(protocol_id: str):
    """Get extracted protocol data for review."""
    protocol = _load_protocol(protocol_id)

    extracted = protocol.get("extracted_json")
    if not extracted:
        return {
            "protocol_id": protocol_id,
            "status": protocol.get("status", "unknown"),
            "extracted_json": None,
            "message": "No extraction data available. Run extraction first.",
        }

    # Identify missing fields (top-level keys with empty values)
    missing_fields = []
    if isinstance(extracted, dict):
        for key, value in extracted.items():
            if value in (None, "", {}, []):
                missing_fields.append(key)

    return {
        "protocol_id": protocol_id,
        "extracted_json": extracted,
        "species": protocol.get("species", "unknown"),
        "missing_fields": missing_fields,
        "filename": protocol.get("filename"),
        "status": protocol.get("status", "unknown"),
    }


@router.patch("/{protocol_id}")
async def update_protocol(protocol_id: str, req: UpdateFieldsRequest):
    """Update extracted protocol fields before generation."""
    protocol = _load_protocol(protocol_id)

    extracted = protocol.get("extracted_json")
    if not extracted or not isinstance(extracted, dict):
        raise HTTPException(status_code=400, detail="No extracted data to update")

    # Apply updates using dot-notation keys
    updated_count = 0
    for key, value in req.sections.items():
        parts = key.split(".")
        target = extracted
        for part in parts[:-1]:
            if isinstance(target, dict) and part in target:
                target = target[part]
            else:
                break
        else:
            if isinstance(target, dict):
                target[parts[-1]] = value
                updated_count += 1

    protocol["extracted_json"] = extracted
    protocol["status"] = "modified"
    _save_protocol(protocol_id, protocol)

    return {
        "protocol_id": protocol_id,
        "updated_fields": updated_count,
        "status": "modified",
    }


@router.post("/{protocol_id}/generate")
async def generate_docx(protocol_id: str, req: GenerateRequest = Body(default=GenerateRequest())):
    """Generate a protocol DOCX from extracted data."""
    protocol = _load_protocol(protocol_id)

    extracted = protocol.get("extracted_json")
    if not extracted or not isinstance(extracted, dict):
        raise HTTPException(status_code=400, detail="No extracted data available. Run extraction first.")

    animal_type = req.template_override or protocol.get("species", "common")

    try:
        result = docx_service.generate(extracted, animal_type)
    except Exception as e:
        logger.error(f"DOCX generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"DOCX generation failed: {str(e)}")

    # Update protocol with generation info
    protocol["generated_file"] = result["file_name"]
    protocol["generated_path"] = result["file_path"]
    protocol["status"] = "generated"
    _save_protocol(protocol_id, protocol)

    return {
        "protocol_id": protocol_id,
        "docx_url": f"/api/files/{protocol_id}/{result['file_name']}",
        "file_name": result["file_name"],
        "file_size": result["file_size"],
        "status": "generated",
    }


# --- File download endpoint ---

@router.get("/{protocol_id}/download")
async def download_docx(protocol_id: str):
    """Download the generated protocol DOCX."""
    protocol = _load_protocol(protocol_id)

    file_path = protocol.get("generated_path")
    if not file_path or not Path(file_path).exists():
        raise HTTPException(status_code=404, detail="Generated DOCX not found. Generate the protocol first.")

    return FileResponse(
        path=file_path,
        filename=protocol.get("generated_file", "protocol.docx"),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
