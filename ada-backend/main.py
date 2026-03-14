"""Ada Backend - Lightweight HTTP server for the Altasciences Ada Protocol Platform.

Uses Python's built-in http.server (no external framework needed).
Provides the same API contract: upload, extract, review, edit, generate.

Usage:
    python3 main.py
    # Server starts on http://0.0.0.0:8000
"""

import http.server
import json
import os
import sys
import uuid
import logging
import re
import time
import mimetypes
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv

# Load .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

# --- Configuration ---
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
DEBUG = os.getenv("DEBUG", "true").lower() == "true"

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2023-11-01-preview")

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR = BASE_DIR / "app" / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
# Frontend location: check Docker mount first, then parent dir
_docker_frontend = BASE_DIR / "frontend"
FRONTEND_DIR = _docker_frontend if _docker_frontend.exists() else BASE_DIR.parent

for d in [OUTPUT_DIR, DATA_DIR, UPLOAD_DIR]:
    d.mkdir(parents=True, exist_ok=True)

AZURE_CONFIGURED = bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY)

# --- Logging ---
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ada-backend")

# --- Import integration modules ---
from app.services.pdf_parser import extract_text
from app.integrations.docx_gen.animal_detector import AnimalDetectorService
from app.integrations.docx_gen.docx_generator import generate_docx_from_json

animal_detector = AnimalDetectorService()

# Import extraction pipeline
orchestrator = None
_parse_json_text = None
try:
    from app.integrations.azure_openai.prompt_helper import PromptOrchestrator
    from app.integrations.azure_openai.prompt_helper import _parse_json_text as parse_fn
    _parse_json_text = parse_fn
    azure_config = {
        "endpoint": AZURE_OPENAI_ENDPOINT,
        "deployment": AZURE_OPENAI_DEPLOYMENT,
        "api_key": AZURE_OPENAI_API_KEY,
        "api_version": AZURE_OPENAI_API_VERSION,
    } if AZURE_CONFIGURED else None
    orchestrator = PromptOrchestrator(azure_config=azure_config)
    logger.info("Extraction pipeline loaded successfully")
except Exception as e:
    logger.error(f"Failed to load extraction pipeline: {e}", exc_info=True)

# Import version/amendment service
from app.services.version_service import (
    create_version, get_versions, get_version_detail, update_version_status,
    create_amendment, get_amendments, get_amendment_detail, update_amendment,
    finalize_amendment, compare_versions, compare_amendment,
    add_audit_entry, get_audit_log,
)

# Import storage backend
from app.services.storage import get_storage_backend

# --- Storage helpers (delegates to configured backend) ---
def save_protocol(protocol_id, data):
    get_storage_backend().save_protocol(protocol_id, data)

def load_protocol(protocol_id):
    return get_storage_backend().load_protocol(protocol_id)


# --- HTTP Request Handler ---
class AdaHandler(http.server.BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status, detail):
        self._send_json({"detail": detail}, status)

    def _send_file(self, filepath, filename=None):
        path = Path(filepath)
        if not path.exists():
            self._send_error(404, "File not found")
            return
        mime, _ = mimetypes.guess_type(str(path))
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Access-Control-Allow-Origin", "*")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, filepath):
        path = Path(filepath)
        if not path.exists():
            self._send_error(404, "Page not found")
            return
        with open(path, "r", encoding="utf-8") as f:
            data = f.read().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        body = self.rfile.read(content_length)
        try:
            return json.loads(body)
        except Exception:
            return {}

    def _parse_multipart(self):
        """Parse multipart/form-data file upload."""
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            return None, None

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # Parse boundary from content type
        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part.split("=", 1)[1].strip().strip('"')
                break

        if not boundary:
            return None, None

        # Split by boundary
        boundary_bytes = f"--{boundary}".encode()
        parts = body.split(boundary_bytes)

        for part in parts:
            if b"filename=" in part:
                header_end = part.find(b"\r\n\r\n")
                if header_end == -1:
                    continue
                header = part[:header_end].decode("utf-8", errors="replace")
                file_data = part[header_end + 4:]
                # Remove trailing boundary markers
                if file_data.endswith(b"\r\n"):
                    file_data = file_data[:-2]
                if file_data.endswith(b"--\r\n"):
                    file_data = file_data[:-4]
                if file_data.endswith(b"--"):
                    file_data = file_data[:-2]

                match = re.search(r'filename="([^"]+)"', header)
                filename = match.group(1) if match else "upload"
                return filename, file_data

        return None, None

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # Health check
        if path == "/api/health":
            templates = [f.name for f in TEMPLATES_DIR.glob("*.docx")] if TEMPLATES_DIR.exists() else []
            storage_type = os.getenv("STORAGE_TYPE", "file")
            self._send_json({
                "status": "ok",
                "azure_configured": AZURE_CONFIGURED,
                "templates_available": templates,
                "mock_mode": not AZURE_CONFIGURED,
                "storage": storage_type,
            })
            return

        # Config
        if path == "/api/config":
            self._send_json({
                "azure_configured": AZURE_CONFIGURED,
                "mock_mode": not AZURE_CONFIGURED,
                "supported_species": ["rat", "dog", "swine"],
                "supported_file_types": [".pdf", ".docx"],
            })
            return

        # Download generated DOCX: /api/protocols/{id}/download
        m = re.match(r"^/api/protocols/([^/]+)/download$", path)
        if m:
            protocol_id = m.group(1)
            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, "Protocol not found")
                return
            file_path = protocol.get("generated_path")
            if not file_path or not Path(file_path).exists():
                self._send_error(404, "Generated DOCX not found")
                return
            self._send_file(file_path, protocol.get("generated_file", "protocol.docx"))
            return

        # Get protocol: /api/protocols/{id}
        m = re.match(r"^/api/protocols/([^/]+)$", path)
        if m:
            protocol_id = m.group(1)
            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, f"Protocol {protocol_id} not found")
                return

            extracted = protocol.get("extracted_json")
            missing_fields = []
            if isinstance(extracted, dict):
                for key, value in extracted.items():
                    if value in (None, "", {}, []):
                        missing_fields.append(key)

            self._send_json({
                "protocol_id": protocol_id,
                "extracted_json": extracted,
                "species": protocol.get("species", "unknown"),
                "missing_fields": missing_fields,
                "filename": protocol.get("filename"),
                "status": protocol.get("status", "unknown"),
            })
            return

        # ===== VERSION & AMENDMENT GET ENDPOINTS =====

        # List versions: /api/protocols/{id}/versions
        m = re.match(r"^/api/protocols/([^/]+)/versions$", path)
        if m:
            protocol_id = m.group(1)
            versions = get_versions(protocol_id)
            self._send_json({"protocol_id": protocol_id, "versions": versions})
            return

        # Get specific version: /api/protocols/{id}/versions/{version}
        m = re.match(r"^/api/protocols/([^/]+)/versions/(v[\d.]+)$", path)
        if m:
            protocol_id, version_num = m.group(1), m.group(2)
            ver = get_version_detail(protocol_id, version_num)
            if not ver:
                self._send_error(404, f"Version {version_num} not found")
                return
            self._send_json(ver)
            return

        # List amendments: /api/protocols/{id}/amendments
        m = re.match(r"^/api/protocols/([^/]+)/amendments$", path)
        if m:
            protocol_id = m.group(1)
            amendments = get_amendments(protocol_id)
            self._send_json({"protocol_id": protocol_id, "amendments": amendments})
            return

        # Get specific amendment: /api/protocols/{id}/amendments/{num}
        m = re.match(r"^/api/protocols/([^/]+)/amendments/(\d+)$", path)
        if m:
            protocol_id, amend_num = m.group(1), int(m.group(2))
            amendment = get_amendment_detail(protocol_id, amend_num)
            if not amendment:
                self._send_error(404, f"Amendment {amend_num} not found")
                return
            self._send_json(amendment)
            return

        # Compare versions: /api/protocols/{id}/diff?a=v0.1&b=v0.2
        m = re.match(r"^/api/protocols/([^/]+)/diff$", path)
        if m:
            protocol_id = m.group(1)
            params = parse_qs(parsed.query)
            va = params.get("a", [None])[0]
            vb = params.get("b", [None])[0]
            if not va or not vb:
                self._send_error(400, "Missing query params: ?a=v0.1&b=v0.2")
                return
            try:
                diff = compare_versions(protocol_id, va, vb)
                self._send_json(diff)
            except ValueError as e:
                self._send_error(404, str(e))
            return

        # Compare amendment vs base: /api/protocols/{id}/amendments/{num}/diff
        m = re.match(r"^/api/protocols/([^/]+)/amendments/(\d+)/diff$", path)
        if m:
            protocol_id, amend_num = m.group(1), int(m.group(2))
            try:
                diff = compare_amendment(protocol_id, amend_num)
                self._send_json(diff)
            except ValueError as e:
                self._send_error(404, str(e))
            return

        # Audit log: /api/protocols/{id}/audit
        m = re.match(r"^/api/protocols/([^/]+)/audit$", path)
        if m:
            protocol_id = m.group(1)
            log = get_audit_log(protocol_id)
            self._send_json({"protocol_id": protocol_id, "entries": log})
            return

        # Serve generated files: /api/files/{filename}
        if path.startswith("/api/files/"):
            file_rel = path[len("/api/files/"):]
            file_path = OUTPUT_DIR / file_rel
            if file_path.exists():
                self._send_file(file_path)
            else:
                self._send_error(404, "File not found")
            return

        # Serve frontend
        if path in ("", "/", "/index.html"):
            frontend_file = FRONTEND_DIR / "ada-platform-v3.html"
            if frontend_file.exists():
                self._send_html(frontend_file)
            else:
                self._send_json({"message": "Ada Backend API running. Frontend not found."})
            return

        self._send_error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # Upload SOW: /api/protocols/upload
        if path == "/api/protocols/upload":
            filename, file_data = self._parse_multipart()
            if not filename or not file_data:
                self._send_error(400, "No file uploaded")
                return

            ext = Path(filename).suffix.lower()
            if ext not in (".pdf", ".docx", ".doc"):
                self._send_error(400, f"Unsupported file type: {ext}")
                return

            upload_id = str(uuid.uuid4())[:8]
            upload_path = UPLOAD_DIR / f"{upload_id}{ext}"
            with open(upload_path, "wb") as f:
                f.write(file_data)
            logger.info(f"Saved upload {upload_id}: {filename} ({len(file_data)} bytes)")

            try:
                document_text = extract_text(str(upload_path))
            except Exception as e:
                logger.error(f"Text extraction failed: {e}", exc_info=True)
                self._send_error(500, f"Failed to extract text: {str(e)}")
                return

            search_text = f"{filename} {document_text[:2000]}"
            species = animal_detector.detect_animal(search_text)

            protocol_data = {
                "upload_id": upload_id,
                "filename": filename,
                "file_path": str(upload_path),
                "document_text": document_text,
                "species_detected": species,
                "text_length": len(document_text),
                "status": "pending_extraction",
                "extracted_json": None,
            }
            save_protocol(upload_id, protocol_data)

            self._send_json({
                "upload_id": upload_id,
                "filename": filename,
                "species_detected": species,
                "text_length": len(document_text),
                "status": "pending_extraction",
            })
            return

        # Extract: /api/protocols/{id}/extract
        m = re.match(r"^/api/protocols/([^/]+)/extract$", path)
        if m:
            protocol_id = m.group(1)
            body = self._read_body()
            animal_type = body.get("animal_type", "")

            if animal_type not in ("rat", "dog", "swine"):
                self._send_error(400, f"Invalid animal type: {animal_type}")
                return

            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, f"Protocol {protocol_id} not found")
                return

            document_text = protocol.get("document_text", "")
            if not document_text:
                self._send_error(400, "No document text available")
                return

            if not orchestrator:
                self._send_error(500, "Extraction pipeline not loaded")
                return

            try:
                start = time.time()
                extracted = orchestrator.generate_model_json(document_text, animal_type)
                elapsed_ms = int((time.time() - start) * 1000)

                if isinstance(extracted, str) and _parse_json_text:
                    extracted = _parse_json_text(extracted)
                    if isinstance(extracted, str):
                        extracted = {"_raw_response": extracted}

                mock_mode = not AZURE_CONFIGURED

                protocol["extracted_json"] = extracted
                protocol["species"] = animal_type
                protocol["extraction_time_ms"] = elapsed_ms
                protocol["mock_mode"] = mock_mode
                protocol["status"] = "extraction_complete"
                save_protocol(protocol_id, protocol)

                # Auto-create initial version snapshot (v0.1)
                try:
                    ver = create_version(
                        protocol_id=protocol_id,
                        extracted_json=extracted,
                        author="Ada (auto-generated)",
                        label="Initial extraction from SOW",
                        status="Draft",
                    )
                    logger.info(f"Auto-created version {ver['version']} for {protocol_id}")
                except Exception as ve:
                    logger.warning(f"Version creation failed (non-fatal): {ve}")

                self._send_json({
                    "protocol_id": protocol_id,
                    "extracted_json": extracted,
                    "extraction_time_ms": elapsed_ms,
                    "mock_mode": mock_mode,
                    "status": "extraction_complete",
                })
            except Exception as e:
                logger.error(f"Extraction failed: {e}", exc_info=True)
                self._send_error(500, f"Extraction failed: {str(e)}")
            return

        # Generate DOCX: /api/protocols/{id}/generate
        m = re.match(r"^/api/protocols/([^/]+)/generate$", path)
        if m:
            protocol_id = m.group(1)
            body = self._read_body()

            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, f"Protocol {protocol_id} not found")
                return

            extracted = protocol.get("extracted_json")
            if not extracted or not isinstance(extracted, dict):
                self._send_error(400, "No extracted data. Run extraction first.")
                return

            animal_type = body.get("template_override") or protocol.get("species", "common")

            try:
                output_path = generate_docx_from_json(
                    data=extracted,
                    animal_type=animal_type,
                    templates_dir=TEMPLATES_DIR,
                    output_dir=OUTPUT_DIR,
                )

                file_path = Path(output_path)
                file_name = file_path.name
                file_size = file_path.stat().st_size if file_path.exists() else 0

                protocol["generated_file"] = file_name
                protocol["generated_path"] = str(file_path.absolute())
                protocol["status"] = "generated"
                save_protocol(protocol_id, protocol)

                self._send_json({
                    "protocol_id": protocol_id,
                    "docx_url": f"/api/files/{file_name}",
                    "file_name": file_name,
                    "file_size": file_size,
                    "status": "generated",
                })
            except Exception as e:
                logger.error(f"DOCX generation failed: {e}", exc_info=True)
                self._send_error(500, f"DOCX generation failed: {str(e)}")
            return

        # ===== VERSION & AMENDMENT POST ENDPOINTS =====

        # Create version: /api/protocols/{id}/versions
        m = re.match(r"^/api/protocols/([^/]+)/versions$", path)
        if m:
            protocol_id = m.group(1)
            body = self._read_body()
            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, "Protocol not found")
                return
            extracted = protocol.get("extracted_json")
            if not extracted:
                self._send_error(400, "No extracted data to version")
                return
            try:
                ver = create_version(
                    protocol_id=protocol_id,
                    extracted_json=extracted,
                    author=body.get("author", "User"),
                    label=body.get("label", ""),
                    status=body.get("status", "Draft"),
                )
                self._send_json({"version": ver["version"], "timestamp": ver["timestamp"], "status": ver["status"]})
            except Exception as e:
                self._send_error(500, str(e))
            return

        # Update version status: /api/protocols/{id}/versions/{ver}/status
        m = re.match(r"^/api/protocols/([^/]+)/versions/(v[\d.]+)/status$", path)
        if m:
            protocol_id, version_num = m.group(1), m.group(2)
            body = self._read_body()
            new_status = body.get("status")
            if not new_status:
                self._send_error(400, "Missing 'status' in body")
                return
            result = update_version_status(protocol_id, version_num, new_status, body.get("author", "User"))
            if result:
                self._send_json({"version": version_num, "status": new_status})
            else:
                self._send_error(404, f"Version {version_num} not found")
            return

        # Create amendment: /api/protocols/{id}/amendments
        m = re.match(r"^/api/protocols/([^/]+)/amendments$", path)
        if m:
            protocol_id = m.group(1)
            body = self._read_body()
            try:
                amendment = create_amendment(
                    protocol_id=protocol_id,
                    base_version=body.get("base_version"),
                    author=body.get("author", "Study Director"),
                    reason=body.get("reason", ""),
                )
                self._send_json({
                    "amendment_id": amendment["amendment_id"],
                    "amendment_number": amendment["amendment_number"],
                    "base_version": amendment["base_version"],
                    "status": amendment["status"],
                })
            except ValueError as e:
                self._send_error(400, str(e))
            except Exception as e:
                self._send_error(500, str(e))
            return

        # Update amendment fields: /api/protocols/{id}/amendments/{num}/edit
        m = re.match(r"^/api/protocols/([^/]+)/amendments/(\d+)/edit$", path)
        if m:
            protocol_id, amend_num = m.group(1), int(m.group(2))
            body = self._read_body()
            sections = body.get("sections", {})
            if not sections:
                self._send_error(400, "No sections to update")
                return
            try:
                result = update_amendment(
                    protocol_id=protocol_id,
                    amendment_number=amend_num,
                    sections=sections,
                    author=body.get("author", "Study Director"),
                )
                self._send_json(result)
            except ValueError as e:
                self._send_error(404, str(e))
            except Exception as e:
                self._send_error(500, str(e))
            return

        # Finalize amendment: /api/protocols/{id}/amendments/{num}/finalize
        m = re.match(r"^/api/protocols/([^/]+)/amendments/(\d+)/finalize$", path)
        if m:
            protocol_id, amend_num = m.group(1), int(m.group(2))
            body = self._read_body()
            try:
                result = finalize_amendment(protocol_id, amend_num, body.get("author", "Study Director"))
                self._send_json(result)
            except ValueError as e:
                self._send_error(404, str(e))
            return

        self._send_error(404, "Not found")

    def do_PATCH(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        m = re.match(r"^/api/protocols/([^/]+)$", path)
        if m:
            protocol_id = m.group(1)
            body = self._read_body()
            sections = body.get("sections", {})

            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, f"Protocol {protocol_id} not found")
                return

            extracted = protocol.get("extracted_json")
            if not extracted or not isinstance(extracted, dict):
                self._send_error(400, "No extracted data to update")
                return

            updated_count = 0
            for key, value in sections.items():
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
            save_protocol(protocol_id, protocol)

            # Auto-create version snapshot on edit
            author = body.get("author", "User")
            try:
                ver = create_version(
                    protocol_id=protocol_id,
                    extracted_json=extracted,
                    author=author,
                    label=f"Edited {updated_count} field(s)",
                    status="In Review",
                )
                logger.info(f"Auto-versioned {protocol_id} → {ver['version']}")
            except Exception as ve:
                logger.warning(f"Version snapshot failed (non-fatal): {ve}")

            # Log field-level changes in audit
            for key, value in sections.items():
                add_audit_entry(
                    protocol_id=protocol_id,
                    action="field_edited",
                    author=author,
                    details={"field": key, "new_value": str(value)[:200]},
                )

            self._send_json({
                "protocol_id": protocol_id,
                "updated_fields": updated_count,
                "status": "modified",
            })
            return

        self._send_error(404, "Not found")

    def log_message(self, format, *args):
        logger.info(f"{self.client_address[0]} - {format % args}")


def main():
    server = http.server.HTTPServer((HOST, PORT), AdaHandler)
    storage_type = os.getenv("STORAGE_TYPE", "file")
    logger.info("=" * 60)
    logger.info(f"Ada Backend starting on http://{HOST}:{PORT}")
    logger.info(f"Azure OpenAI configured: {AZURE_CONFIGURED}")
    logger.info(f"Mock mode: {not AZURE_CONFIGURED}")
    logger.info(f"Storage backend: {storage_type}")
    logger.info(f"Templates: {TEMPLATES_DIR}")
    logger.info(f"Frontend: {FRONTEND_DIR / 'ada-platform-v3.html'}")
    logger.info("=" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
