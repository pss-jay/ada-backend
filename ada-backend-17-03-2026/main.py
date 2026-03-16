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

# Import auth service
from app.services.auth_service import (
    create_token, hash_password, verify_password,
    extract_auth_from_header, AuthContext,
)

# Import V.1 features: Gap Detection & Section Management
from app.services.gap_detection_service import GapDetectionService
from app.services.section_service import SectionService

gap_detector = GapDetectionService()
section_service = SectionService()

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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
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
        self.send_header("Access-Control-Expose-Headers", "Content-Disposition")
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
        """Parse multipart/form-data file upload. Returns first file found."""
        files = self._parse_multipart_all()
        if files:
            return files[0]["filename"], files[0]["data"]
        return None, None

    def _parse_multipart_all(self):
        """Parse multipart/form-data and return ALL files and form fields."""
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            return []

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part.split("=", 1)[1].strip().strip('"')
                break

        if not boundary:
            return []

        boundary_bytes = f"--{boundary}".encode()
        parts = body.split(boundary_bytes)
        result = []

        for part in parts:
            if b"Content-Disposition" not in part:
                continue
            header_end = part.find(b"\r\n\r\n")
            if header_end == -1:
                continue
            header = part[:header_end].decode("utf-8", errors="replace")
            file_data = part[header_end + 4:]
            if file_data.endswith(b"\r\n"):
                file_data = file_data[:-2]
            if file_data.endswith(b"--\r\n"):
                file_data = file_data[:-4]
            if file_data.endswith(b"--"):
                file_data = file_data[:-2]

            name_match = re.search(r'name="([^"]+)"', header)
            field_name = name_match.group(1) if name_match else ""

            if b"filename=" in part.split(b"\r\n\r\n")[0]:
                fname_match = re.search(r'filename="([^"]+)"', header)
                filename = fname_match.group(1) if fname_match else "upload"
                result.append({"type": "file", "field_name": field_name, "filename": filename, "data": file_data})
            else:
                result.append({"type": "field", "field_name": field_name, "value": file_data.decode("utf-8", errors="replace")})

        return result

    # --- Auth helpers ---

    def _get_auth(self) -> AuthContext:
        """Extract auth from Authorization header. Returns AuthContext or None (sends 401)."""
        auth_header = self.headers.get("Authorization", "")
        ctx = extract_auth_from_header(auth_header)
        if not ctx:
            self._send_error(401, "Missing or invalid Authorization token")
        return ctx

    def _require_admin(self) -> AuthContext:
        """Require admin role. Returns AuthContext or None (sends 403)."""
        ctx = self._get_auth()
        if ctx and not ctx.is_admin():
            self._send_error(403, "Admin role required")
            return None
        return ctx

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # Health check
        if path == "/api/health":
            templates = [f.name for f in TEMPLATES_DIR.glob("*.docx")] if TEMPLATES_DIR.exists() else []
            storage_type = os.getenv("STORAGE_TYPE", "file")

            # Verify storage connectivity
            storage_ok = True
            storage_error = None
            try:
                backend = get_storage_backend()
                # Quick read test to verify connectivity
                backend.load_protocol("__health_check__")
            except Exception as e:
                storage_ok = False
                storage_error = str(e)

            self._send_json({
                "status": "ok" if storage_ok else "degraded",
                "azure_configured": AZURE_CONFIGURED,
                "templates_available": templates,
                "mock_mode": not AZURE_CONFIGURED,
                "storage": storage_type,
                "storage_connected": storage_ok,
                "storage_error": storage_error,
            })
            return

        # Get current user: /api/auth/me
        if path == "/api/auth/me":
            ctx = self._get_auth()
            if not ctx:
                return
            self._send_json({
                "username": ctx.username,
                "email": ctx.email,
                "role": ctx.role,
                "full_name": ctx.full_name,
            })
            return

        # List users: /api/auth/users (admin only)
        if path == "/api/auth/users":
            ctx = self._require_admin()
            if not ctx:
                return
            users = get_storage_backend().load_all_users()
            summaries = [{
                "username": u.get("username"),
                "email": u.get("email", ""),
                "full_name": u.get("full_name", ""),
                "role": u.get("role", "operator"),
                "is_active": u.get("is_active", True),
                "created_at": u.get("created_at"),
                "last_login": u.get("last_login"),
            } for u in users]
            self._send_json({"users": summaries})
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

        # ===== ALL REMAINING GET ENDPOINTS REQUIRE AUTH =====
        # (except frontend, health, config above)
        if path.startswith("/api/") and path not in ("/api/health", "/api/config"):
            ctx = self._get_auth()
            if not ctx:
                return

        # List all protocols: /api/protocols
        if path == "/api/protocols":
            backend = get_storage_backend()
            protocols = backend.load_all_protocols()
            # Enrich with version count and amendment count
            for p in protocols:
                pid = p["protocol_id"]
                versions = get_versions(pid)
                amendments = get_amendments(pid)
                p["version_count"] = len(versions)
                p["amendment_count"] = len(amendments)
                p["latest_version"] = versions[-1]["version"] if versions else "v0.0"
                p["latest_version_status"] = versions[-1].get("status", "Draft") if versions else "Draft"
                # Determine display status based on FRD lifecycle: Draft / Updated / Final / Amendment
                if amendments:
                    p["display_status"] = f"Amendment {len(amendments)}"
                elif any(v.get("status") == "Final" for v in versions):
                    p["display_status"] = "Final"
                elif len(versions) > 1:
                    p["display_status"] = "Updated"
                else:
                    p["display_status"] = "Draft"
            self._send_json({"protocols": protocols})
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

        # Get pending changes: /api/protocols/{id}/pending-changes
        m = re.match(r"^/api/protocols/([^/]+)/pending-changes$", path)
        if m:
            protocol_id = m.group(1)
            pending = get_storage_backend().load_pending_changes(protocol_id)
            if not pending:
                self._send_error(404, "No pending changes")
                return
            self._send_json(pending)
            return

        # Download latest protocol as DOCX: /api/protocols/{id}/download-docx
        m = re.match(r"^/api/protocols/([^/]+)/download-docx$", path)
        if m:
            protocol_id = m.group(1)
            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, "Protocol not found")
                return
            extracted = protocol.get("extracted_json")
            if not extracted:
                self._send_error(400, "No extracted data. Run extraction first.")
                return
            animal_type = protocol.get("species", "rat")
            try:
                output_path = generate_docx_from_json(
                    data=extracted,
                    animal_type=animal_type,
                    templates_dir=TEMPLATES_DIR,
                    output_dir=OUTPUT_DIR,
                )
                fname = f"protocol_{protocol_id}.docx"
                self._send_file(output_path, fname)
            except Exception as e:
                logger.error(f"DOCX download generation failed: {e}", exc_info=True)
                self._send_error(500, f"Failed to generate DOCX: {str(e)}")
            return

        # Audit log: /api/protocols/{id}/audit
        m = re.match(r"^/api/protocols/([^/]+)/audit$", path)
        if m:
            protocol_id = m.group(1)
            log = get_audit_log(protocol_id)
            self._send_json({"protocol_id": protocol_id, "entries": log})
            return

        # ===== GAP DETECTION & SMART PARSING ENDPOINTS =====

        # Get gap analysis: /api/protocols/{id}/gaps
        m = re.match(r"^/api/protocols/([^/]+)/gaps$", path)
        if m:
            protocol_id = m.group(1)
            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, "Protocol not found")
                return
            extracted = protocol.get("extracted_json")
            if not extracted:
                self._send_error(400, "No extracted data. Run extraction first.")
                return
            analysis = gap_detector.analyze(extracted)
            self._send_json({"protocol_id": protocol_id, **analysis})
            return

        # Get Q&A wizard questions: /api/protocols/{id}/questions
        m = re.match(r"^/api/protocols/([^/]+)/questions$", path)
        if m:
            protocol_id = m.group(1)
            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, "Protocol not found")
                return
            extracted = protocol.get("extracted_json")
            if not extracted:
                self._send_error(400, "No extracted data.")
                return
            analysis = gap_detector.analyze(extracted)
            questions = gap_detector.generate_questions(analysis)
            params = parse_qs(parsed.query)
            sort_by = params.get("sort", ["criticality"])[0]
            if sort_by == "section":
                questions.sort(key=lambda q: q.get("field_path", ""))
            self._send_json({"protocol_id": protocol_id, "questions": questions, "total": len(questions)})
            return

        # Get confidence scores: /api/protocols/{id}/confidence
        m = re.match(r"^/api/protocols/([^/]+)/confidence$", path)
        if m:
            protocol_id = m.group(1)
            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, "Protocol not found")
                return
            extracted = protocol.get("extracted_json")
            if not extracted:
                self._send_error(400, "No extracted data.")
                return
            analysis = gap_detector.analyze(extracted)
            params = parse_qs(parsed.query)
            threshold = float(params.get("threshold", [0.0])[0])
            fields = analysis.get("fields", [])
            if threshold > 0:
                fields = [f for f in fields if f.get("status") != "extracted" or True]
            self._send_json({
                "protocol_id": protocol_id,
                "completeness_score": analysis.get("completeness_score", 0),
                "summary": analysis.get("summary", {}),
                "fields": fields,
            })
            return

        # ===== SECTION-LEVEL EDITING & LOCKING GET ENDPOINTS =====

        # List all sections with status: /api/protocols/{id}/sections
        m = re.match(r"^/api/protocols/([^/]+)/sections$", path)
        if m:
            protocol_id = m.group(1)
            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, "Protocol not found")
                return
            sections = section_service.get_sections_status(protocol_id)
            self._send_json({"protocol_id": protocol_id, "sections": sections})
            return

        # Get section detail: /api/protocols/{id}/sections/{section}
        m = re.match(r"^/api/protocols/([^/]+)/sections/([A-Za-z]+(?:and[A-Za-z]*)*)$", path)
        if m:
            protocol_id, section_id = m.group(1), m.group(2)
            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, "Protocol not found")
                return
            detail = section_service.get_section_detail(protocol_id, section_id)
            if not detail:
                self._send_error(404, f"Section {section_id} not found")
                return
            self._send_json(detail)
            return

        # Get comments for section: /api/protocols/{id}/sections/{section}/comments
        m = re.match(r"^/api/protocols/([^/]+)/sections/([A-Za-z]+(?:and[A-Za-z]*)*)/comments$", path)
        if m:
            protocol_id, section_id = m.group(1), m.group(2)
            params = parse_qs(parsed.query)
            status_filter = params.get("status", ["all"])[0]
            comments = section_service.get_comments(protocol_id, section_id, status_filter)
            self._send_json({"protocol_id": protocol_id, "section": section_id, "comments": comments})
            return

        # Get active users / presence: /api/protocols/{id}/presence
        m = re.match(r"^/api/protocols/([^/]+)/presence$", path)
        if m:
            protocol_id = m.group(1)
            presence = section_service.get_presence(protocol_id)
            self._send_json({"protocol_id": protocol_id, **presence})
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

        # ===== AUTH ENDPOINTS (public) =====

        # Login: /api/auth/login
        if path == "/api/auth/login":
            body = self._read_body()
            username = body.get("username", "").strip()
            password = body.get("password", "")
            if not username or not password:
                self._send_error(400, "Username and password required")
                return
            storage = get_storage_backend()
            user = storage.load_user(username)
            if not user or not verify_password(password, user.get("password_hash", "")):
                self._send_error(401, "Invalid credentials")
                return
            if not user.get("is_active", True):
                self._send_error(403, "Account is inactive")
                return
            from datetime import datetime
            token = create_token(username, user.get("email", ""), user.get("role", "operator"), user.get("full_name", ""))
            user["last_login"] = datetime.utcnow().isoformat() + "Z"
            storage.save_user(user)
            self._send_json({
                "token": token,
                "username": username,
                "email": user.get("email", ""),
                "full_name": user.get("full_name", ""),
                "role": user.get("role", "operator"),
            })
            return

        # Create user: /api/auth/users (admin only)
        if path == "/api/auth/users":
            ctx = self._require_admin()
            if not ctx:
                return
            body = self._read_body()
            username = body.get("username", "").strip()
            email = body.get("email", "").strip()
            password = body.get("password", "")
            full_name = body.get("full_name", "")
            role = body.get("role", "operator")
            if not username or not password or not email:
                self._send_error(400, "username, email, and password are required")
                return
            storage = get_storage_backend()
            if storage.load_user(username):
                self._send_error(409, "User already exists")
                return
            from datetime import datetime
            now = datetime.utcnow().isoformat() + "Z"
            user_data = {
                "username": username,
                "email": email,
                "full_name": full_name,
                "role": role,
                "password_hash": hash_password(password),
                "is_active": True,
                "created_at": now,
                "last_login": None,
            }
            storage.save_user(user_data)
            add_audit_entry(
                protocol_id="system",
                action="user_created",
                author=ctx.username,
                details={"new_user": username, "role": role},
            )
            self._send_json({"username": username, "email": email, "role": role, "created_at": now}, status=201)
            return

        # ===== ALL REMAINING POST ENDPOINTS REQUIRE AUTH =====

        ctx = self._get_auth()
        if not ctx:
            return

        # Upload SOW and/or Questionnaire: /api/protocols/upload
        # Supports single or multiple file upload in one request
        if path == "/api/protocols/upload":
            all_parts = self._parse_multipart_all()
            files = [p for p in all_parts if p.get("type") == "file"]

            if not files:
                self._send_error(400, "No files uploaded. Please upload at least one document (SOW or Sponsor Questionnaire).")
                return

            # Validate all files before processing any
            ALLOWED_EXTENSIONS = (".pdf", ".docx", ".doc")
            MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB per file
            MIN_FILE_SIZE = 100  # Minimum 100 bytes (reject empty files)

            validated_files = []
            for f_part in files:
                fname = f_part["filename"]
                fdata = f_part["data"]
                ext = Path(fname).suffix.lower()

                if ext not in ALLOWED_EXTENSIONS:
                    self._send_error(400, f"Unsupported file type '{ext}' for file '{fname}'. Accepted: PDF, DOCX, DOC.")
                    return
                if len(fdata) > MAX_FILE_SIZE:
                    self._send_error(400, f"File '{fname}' exceeds 50MB size limit ({len(fdata) // (1024*1024)}MB).")
                    return
                if len(fdata) < MIN_FILE_SIZE:
                    self._send_error(400, f"File '{fname}' appears to be empty or corrupt ({len(fdata)} bytes).")
                    return

                # Quick format validation: check magic bytes
                if ext == ".pdf" and not fdata[:5].startswith(b"%PDF"):
                    self._send_error(400, f"File '{fname}' does not appear to be a valid PDF.")
                    return
                if ext in (".docx", ".doc") and not fdata[:2] == b"PK":
                    self._send_error(400, f"File '{fname}' does not appear to be a valid Word document.")
                    return

                validated_files.append({"filename": fname, "data": fdata, "ext": ext, "field_name": f_part.get("field_name", "file")})

            upload_id = str(uuid.uuid4())[:8]

            # Save all files to disk and extract text from each
            combined_text_parts = []
            file_records = []
            for idx, vf in enumerate(validated_files):
                file_suffix = f"_{idx}" if len(validated_files) > 1 else ""
                upload_path = UPLOAD_DIR / f"{upload_id}{file_suffix}{vf['ext']}"
                with open(upload_path, "wb") as f:
                    f.write(vf["data"])
                logger.info(f"Saved upload {upload_id}{file_suffix}: {vf['filename']} ({len(vf['data'])} bytes)")

                # Extract text from each file
                try:
                    doc_text = extract_text(str(upload_path))
                    if not doc_text or len(doc_text.strip()) < 20:
                        self._send_error(400, f"File '{vf['filename']}' produced no readable text. The file may be image-only or corrupt.")
                        return
                except Exception as e:
                    logger.error(f"Text extraction failed for {vf['filename']}: {e}", exc_info=True)
                    self._send_error(500, f"Failed to extract text from '{vf['filename']}': {str(e)}")
                    return

                # Classify document type by field name or content heuristic
                doc_type = vf["field_name"]
                fname_lower = vf["filename"].lower()
                if "questionnaire" in fname_lower or "quest" in fname_lower or "sponsor" in fname_lower:
                    doc_type = "questionnaire"
                elif "sow" in fname_lower or "statement" in fname_lower or "scope" in fname_lower or "work" in fname_lower:
                    doc_type = "sow"
                elif doc_type not in ("sow", "questionnaire"):
                    doc_type = "sow" if idx == 0 else "questionnaire"

                combined_text_parts.append(f"--- BEGIN {doc_type.upper()}: {vf['filename']} ---\n{doc_text}\n--- END {doc_type.upper()} ---")
                file_records.append({
                    "filename": vf["filename"],
                    "file_path": str(upload_path),
                    "doc_type": doc_type,
                    "text_length": len(doc_text),
                })

            # Combine all document text for extraction
            combined_text = "\n\n".join(combined_text_parts)

            # Detect species from combined text of ALL uploaded documents
            search_text = " ".join([vf["filename"] for vf in validated_files]) + " " + combined_text[:5000]
            species = animal_detector.detect_animal(search_text)

            primary_filename = validated_files[0]["filename"]
            all_filenames = [vf["filename"] for vf in validated_files]

            protocol_data = {
                "upload_id": upload_id,
                "filename": primary_filename,
                "all_filenames": all_filenames,
                "files": file_records,
                "file_count": len(validated_files),
                "document_text": combined_text,
                "species_detected": species,
                "text_length": len(combined_text),
                "status": "pending_extraction",
                "extracted_json": None,
            }
            save_protocol(upload_id, protocol_data)

            logger.info(f"Protocol {upload_id}: {len(validated_files)} file(s) uploaded, species={species}, total_text={len(combined_text)} chars")

            self._send_json({
                "upload_id": upload_id,
                "filename": primary_filename,
                "all_filenames": all_filenames,
                "file_count": len(validated_files),
                "files": [{"filename": fr["filename"], "doc_type": fr["doc_type"], "text_length": fr["text_length"]} for fr in file_records],
                "species_detected": species,
                "text_length": len(combined_text),
                "status": "pending_extraction",
            })
            return

        # Extract: /api/protocols/{id}/extract
        m = re.match(r"^/api/protocols/([^/]+)/extract$", path)
        if m:
            protocol_id = m.group(1)
            body = self._read_body()

            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, f"Protocol {protocol_id} not found")
                return

            # Use explicitly provided animal_type, or fall back to detected species from upload
            animal_type = body.get("animal_type", "")
            if not animal_type or animal_type == "common":
                animal_type = protocol.get("species_detected", "")
            if animal_type not in ("rat", "dog", "swine"):
                # Try re-detecting from document text
                doc_text = protocol.get("document_text", "")
                if doc_text:
                    animal_type = animal_detector.detect_animal(doc_text[:5000])
                if animal_type not in ("rat", "dog", "swine"):
                    self._send_error(400,
                        f"Could not determine species (detected: '{animal_type}'). "
                        f"Please specify animal_type as 'rat', 'dog', or 'swine' in the request body.")
                    return

            document_text = protocol.get("document_text", "")
            if not document_text:
                self._send_error(400, "No document text available. Upload a document first.")
                return

            if not orchestrator:
                self._send_error(500, "Extraction pipeline not loaded. Check server configuration.")
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
                        author=f"Ada (triggered by {ctx.username})",
                        label="Initial extraction from SOW",
                        status="Draft",
                    )
                    logger.info(f"Auto-created version {ver['version']} for {protocol_id}")
                except Exception as ve:
                    logger.warning(f"Version creation failed (non-fatal): {ve}")

                # Auto-run gap analysis on extraction
                gap_analysis = None
                try:
                    gap_analysis = gap_detector.analyze(extracted)
                    logger.info(f"Gap analysis: completeness={gap_analysis.get('completeness_score', 0):.0%}, "
                                f"critical_gaps={gap_analysis['summary'].get('missing_critical', 0)}")
                except Exception as ge:
                    logger.warning(f"Gap analysis failed (non-fatal): {ge}")

                self._send_json({
                    "protocol_id": protocol_id,
                    "extracted_json": extracted,
                    "extraction_time_ms": elapsed_ms,
                    "mock_mode": mock_mode,
                    "status": "extraction_complete",
                    "gap_analysis": gap_analysis,
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
                    author=ctx.username,
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
            result = update_version_status(protocol_id, version_num, new_status, ctx.username)
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
                    author=ctx.username,
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
                    author=ctx.username,
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
                result = finalize_amendment(protocol_id, amend_num, ctx.username)
                self._send_json(result)
            except ValueError as e:
                self._send_error(404, str(e))
            return

        # ===== DOCX UPLOAD & CHANGE REVIEW ENDPOINTS =====

        # Upload edited DOCX: /api/protocols/{id}/upload-changes
        m = re.match(r"^/api/protocols/([^/]+)/upload-changes$", path)
        if m:
            protocol_id = m.group(1)
            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, "Protocol not found")
                return
            original_json = protocol.get("extracted_json")
            if not original_json:
                self._send_error(400, "Protocol has no extracted data")
                return

            filename, file_data = self._parse_multipart()
            if not filename or not file_data:
                self._send_error(400, "No file uploaded")
                return
            if not filename.lower().endswith(".docx"):
                self._send_error(400, "Only .docx files are accepted")
                return

            upload_path = UPLOAD_DIR / f"{protocol_id}_edits.docx"
            with open(upload_path, "wb") as f:
                f.write(file_data)

            try:
                from app.services.docx_parser import parse_docx_to_json, compute_upload_diff
                parse_result = parse_docx_to_json(str(upload_path), original_json)
                diff = compute_upload_diff(original_json, parse_result["reconstructed_json"])

                from datetime import datetime
                pending = {
                    "protocol_id": protocol_id,
                    "uploaded_by": ctx.username,
                    "uploaded_at": datetime.utcnow().isoformat() + "Z",
                    "original_json": original_json,
                    "parsed_json": parse_result["reconstructed_json"],
                    "confidence": parse_result["confidence"],
                    "warnings": parse_result["warnings"],
                    "diff": diff,
                }
                get_storage_backend().save_pending_changes(protocol_id, pending)

                add_audit_entry(
                    protocol_id=protocol_id,
                    action="docx_uploaded_for_review",
                    author=ctx.username,
                    details={
                        "filename": filename,
                        "confidence": round(parse_result["confidence"], 2),
                        "total_changes": diff["summary"]["total_changes"],
                    },
                )

                self._send_json({
                    "protocol_id": protocol_id,
                    "status": "pending_review",
                    "confidence": parse_result["confidence"],
                    "warnings": parse_result["warnings"],
                    "diff": diff,
                })
            except Exception as e:
                logger.error(f"DOCX upload processing failed: {e}", exc_info=True)
                self._send_error(500, f"Failed to process DOCX: {str(e)}")
            finally:
                try:
                    upload_path.unlink()
                except Exception:
                    pass
            return

        # Accept ALL pending changes: /api/protocols/{id}/accept-all-changes
        m = re.match(r"^/api/protocols/([^/]+)/accept-all-changes$", path)
        if m:
            protocol_id = m.group(1)
            pending = get_storage_backend().load_pending_changes(protocol_id)
            if not pending:
                self._send_error(404, "No pending changes")
                return
            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, "Protocol not found")
                return
            try:
                parsed_json = pending["parsed_json"]
                ver = create_version(
                    protocol_id=protocol_id,
                    extracted_json=parsed_json,
                    author=ctx.username,
                    label="All changes accepted from re-uploaded DOCX",
                    status="In Review",
                )
                protocol["extracted_json"] = parsed_json
                protocol["status"] = "modified"
                save_protocol(protocol_id, protocol)
                get_storage_backend().delete_pending_changes(protocol_id)

                add_audit_entry(
                    protocol_id=protocol_id,
                    action="docx_changes_accepted_all",
                    author=ctx.username,
                    details={
                        "new_version": ver["version"],
                        "total_changes": pending["diff"]["summary"]["total_changes"],
                    },
                )
                self._send_json({
                    "protocol_id": protocol_id,
                    "status": "changes_applied",
                    "new_version": ver["version"],
                })
            except Exception as e:
                logger.error(f"Accept all changes failed: {e}", exc_info=True)
                self._send_error(500, str(e))
            return

        # Accept SELECTED changes: /api/protocols/{id}/accept-changes
        m = re.match(r"^/api/protocols/([^/]+)/accept-changes$", path)
        if m:
            protocol_id = m.group(1)
            body = self._read_body()
            field_keys = body.get("fields", [])
            if not field_keys:
                self._send_error(400, "No fields specified")
                return
            pending = get_storage_backend().load_pending_changes(protocol_id)
            if not pending:
                self._send_error(404, "No pending changes")
                return
            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, "Protocol not found")
                return
            try:
                from app.services.docx_parser import selective_merge
                merged = selective_merge(pending["original_json"], pending["parsed_json"], field_keys)
                ver = create_version(
                    protocol_id=protocol_id,
                    extracted_json=merged,
                    author=ctx.username,
                    label=f"Accepted {len(field_keys)} change(s) from re-uploaded DOCX",
                    status="In Review",
                )
                protocol["extracted_json"] = merged
                protocol["status"] = "modified"
                save_protocol(protocol_id, protocol)
                get_storage_backend().delete_pending_changes(protocol_id)

                add_audit_entry(
                    protocol_id=protocol_id,
                    action="docx_changes_accepted_selective",
                    author=ctx.username,
                    details={
                        "new_version": ver["version"],
                        "fields_accepted": len(field_keys),
                        "fields": field_keys,
                    },
                )
                self._send_json({
                    "protocol_id": protocol_id,
                    "status": "changes_applied",
                    "new_version": ver["version"],
                    "fields_accepted": len(field_keys),
                })
            except Exception as e:
                logger.error(f"Accept selective changes failed: {e}", exc_info=True)
                self._send_error(500, str(e))
            return

        # Reject pending changes: /api/protocols/{id}/reject-changes
        m = re.match(r"^/api/protocols/([^/]+)/reject-changes$", path)
        if m:
            protocol_id = m.group(1)
            pending = get_storage_backend().load_pending_changes(protocol_id)
            if not pending:
                self._send_error(404, "No pending changes")
                return
            get_storage_backend().delete_pending_changes(protocol_id)
            add_audit_entry(
                protocol_id=protocol_id,
                action="docx_changes_rejected",
                author=ctx.username,
                details={"total_changes": pending["diff"]["summary"]["total_changes"]},
            )
            self._send_json({"protocol_id": protocol_id, "status": "changes_rejected"})
            return

        # ===== SMART PARSING POST ENDPOINTS =====

        # Submit Q&A wizard answers: /api/protocols/{id}/answers
        m = re.match(r"^/api/protocols/([^/]+)/answers$", path)
        if m:
            protocol_id = m.group(1)
            body = self._read_body()
            answers = body if isinstance(body, dict) else {}
            if not answers:
                self._send_error(400, "No answers provided")
                return
            protocol = load_protocol(protocol_id)
            if not protocol:
                self._send_error(404, "Protocol not found")
                return
            extracted = protocol.get("extracted_json", {})
            updated_count = 0
            for field_path, value in answers.items():
                parts = field_path.split(".")
                target = extracted
                for part in parts[:-1]:
                    if isinstance(target, dict):
                        if part not in target:
                            target[part] = {}
                        target = target[part]
                    else:
                        break
                else:
                    if isinstance(target, dict):
                        target[parts[-1]] = value
                        updated_count += 1
            protocol["extracted_json"] = extracted
            save_protocol(protocol_id, protocol)
            try:
                ver = create_version(
                    protocol_id=protocol_id,
                    extracted_json=extracted,
                    author=ctx.username,
                    label=f"Q&A Wizard: {updated_count} gap(s) resolved",
                    status="Draft",
                )
            except Exception:
                pass
            add_audit_entry(
                protocol_id=protocol_id,
                action="gaps_resolved_via_wizard",
                author=ctx.username,
                details={"fields_updated": updated_count, "fields": list(answers.keys())},
            )
            # Re-run gap analysis to return updated status
            analysis = gap_detector.analyze(extracted)
            self._send_json({
                "protocol_id": protocol_id,
                "fields_updated": updated_count,
                "completeness_score": analysis.get("completeness_score", 0),
                "remaining_gaps": analysis["summary"].get("missing_critical", 0) + analysis["summary"].get("missing_optional", 0),
            })
            return

        # ===== SECTION-LEVEL EDITING & LOCKING POST ENDPOINTS =====

        # Acquire section lock: /api/protocols/{id}/sections/{section}/lock
        m = re.match(r"^/api/protocols/([^/]+)/sections/([A-Za-z]+(?:and[A-Za-z]*)*)/lock$", path)
        if m:
            protocol_id, section_id = m.group(1), m.group(2)
            result = section_service.acquire_lock(protocol_id, section_id, ctx.username)
            if result.get("error"):
                self._send_error(409, result["error"])
                return
            self._send_json(result)
            return

        # Heartbeat section lock: /api/protocols/{id}/sections/{section}/heartbeat
        m = re.match(r"^/api/protocols/([^/]+)/sections/([A-Za-z]+(?:and[A-Za-z]*)*)/heartbeat$", path)
        if m:
            protocol_id, section_id = m.group(1), m.group(2)
            result = section_service.heartbeat_lock(protocol_id, section_id, ctx.username)
            if result.get("error"):
                self._send_error(403, result["error"])
                return
            self._send_json(result)
            return

        # Force-release section lock (admin): /api/protocols/{id}/sections/{section}/force-release
        m = re.match(r"^/api/protocols/([^/]+)/sections/([A-Za-z]+(?:and[A-Za-z]*)*)/force-release$", path)
        if m:
            protocol_id, section_id = m.group(1), m.group(2)
            if not ctx.is_admin():
                self._send_error(403, "Admin role required")
                return
            result = section_service.force_release_lock(protocol_id, section_id, ctx.username)
            self._send_json(result)
            return

        # Set section status: /api/protocols/{id}/sections/{section}/status
        m = re.match(r"^/api/protocols/([^/]+)/sections/([A-Za-z]+(?:and[A-Za-z]*)*)/status$", path)
        if m:
            protocol_id, section_id = m.group(1), m.group(2)
            body = self._read_body()
            new_status = body.get("status")
            if not new_status:
                self._send_error(400, "Missing 'status' field")
                return
            result = section_service.set_section_status(protocol_id, section_id, new_status, ctx.username)
            if result.get("error"):
                self._send_error(400, result["error"])
                return
            self._send_json(result)
            return

        # Assign reviewer: /api/protocols/{id}/sections/{section}/assign
        m = re.match(r"^/api/protocols/([^/]+)/sections/([A-Za-z]+(?:and[A-Za-z]*)*)/assign$", path)
        if m:
            protocol_id, section_id = m.group(1), m.group(2)
            body = self._read_body()
            reviewer = body.get("reviewer")
            if not reviewer:
                self._send_error(400, "Missing 'reviewer' field")
                return
            result = section_service.assign_reviewer(protocol_id, section_id, reviewer, ctx.username)
            self._send_json(result)
            return

        # Bulk assign reviewers: /api/protocols/{id}/sections/bulk-assign
        m = re.match(r"^/api/protocols/([^/]+)/sections/bulk-assign$", path)
        if m:
            protocol_id = m.group(1)
            body = self._read_body()
            assignments = body.get("assignments", [])
            if not assignments:
                self._send_error(400, "No assignments provided")
                return
            result = section_service.bulk_assign_reviewers(protocol_id, assignments, ctx.username)
            self._send_json(result)
            return

        # Add comment: /api/protocols/{id}/sections/{section}/comments
        m = re.match(r"^/api/protocols/([^/]+)/sections/([A-Za-z]+(?:and[A-Za-z]*)*)/comments$", path)
        if m:
            protocol_id, section_id = m.group(1), m.group(2)
            body = self._read_body()
            field = body.get("field", "")
            text = body.get("text", "")
            parent_id = body.get("parent_id")
            if not text:
                self._send_error(400, "Comment text is required")
                return
            result = section_service.add_comment(protocol_id, section_id, field, text, ctx.username, parent_id)
            self._send_json(result, status=201)
            return

        # Resolve comment: /api/protocols/{id}/comments/{comment_id}/resolve
        m = re.match(r"^/api/protocols/([^/]+)/comments/([^/]+)/resolve$", path)
        if m:
            protocol_id, comment_id = m.group(1), m.group(2)
            result = section_service.resolve_comment(protocol_id, comment_id, ctx.username)
            if result.get("error"):
                self._send_error(404, result["error"])
                return
            self._send_json(result)
            return

        self._send_error(404, "Not found")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        ctx = self._get_auth()
        if not ctx:
            return

        # Release section lock: DELETE /api/protocols/{id}/sections/{section}/lock
        m = re.match(r"^/api/protocols/([^/]+)/sections/([A-Za-z]+(?:and[A-Za-z]*)*)/lock$", path)
        if m:
            protocol_id, section_id = m.group(1), m.group(2)
            result = section_service.release_lock(protocol_id, section_id, ctx.username)
            if result.get("error"):
                self._send_error(403, result["error"])
                return
            self._send_json(result)
            return

        self._send_error(404, "Not found")

    def do_PATCH(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        ctx = self._get_auth()
        if not ctx:
            return

        # Update section fields: PATCH /api/protocols/{id}/sections/{section}
        m = re.match(r"^/api/protocols/([^/]+)/sections/([A-Za-z]+(?:and[A-Za-z]*)*)$", path)
        if m:
            protocol_id, section_id = m.group(1), m.group(2)
            body = self._read_body()
            fields = body.get("fields", {})
            if not fields:
                self._send_error(400, "No fields to update")
                return
            result = section_service.update_section_fields(protocol_id, section_id, fields, ctx.username)
            if result.get("error"):
                self._send_error(result.get("status_code", 400), result["error"])
                return
            # Also update the main protocol record
            protocol = load_protocol(protocol_id)
            if protocol:
                extracted = protocol.get("extracted_json", {})
                section_data = extracted.get(section_id, {})
                if isinstance(section_data, dict):
                    section_data.update(fields)
                    extracted[section_id] = section_data
                    protocol["extracted_json"] = extracted
                    protocol["status"] = "modified"
                    save_protocol(protocol_id, protocol)
            self._send_json(result)
            return

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
            try:
                ver = create_version(
                    protocol_id=protocol_id,
                    extracted_json=extracted,
                    author=ctx.username,
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
                    author=ctx.username,
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


def _ensure_default_admin():
    """Create a default admin user on first startup if no users exist."""
    from datetime import datetime
    storage = get_storage_backend()
    existing = storage.load_all_users()
    if existing:
        return  # Users already exist
    default_pw = os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123")
    admin = {
        "username": "admin",
        "email": "admin@altasciences.com",
        "full_name": "System Administrator",
        "role": "admin",
        "password_hash": hash_password(default_pw),
        "is_active": True,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "last_login": None,
    }
    storage.save_user(admin)
    logger.info(f"Created default admin user (username: admin, password: {default_pw})")
    logger.info("** CHANGE THE DEFAULT PASSWORD IN PRODUCTION **")


def main():
    _ensure_default_admin()
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
