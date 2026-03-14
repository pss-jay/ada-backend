"""File-based storage backend using JSON files.

This is the default backend for local development and Phase 1 deployment.
Data is stored in app/data/ with subdirectories for versions, amendments, and audit.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.storage import StorageBackend
from app.core.config import settings

logger = logging.getLogger(__name__)


class FileStorage(StorageBackend):
    """JSON file storage backend."""

    def __init__(self):
        self.base_dir = settings.DATA_DIR
        self.versions_dir = self.base_dir / "versions"
        self.amendments_dir = self.base_dir / "amendments"
        self.audit_dir = self.base_dir / "audit"

        for d in [self.base_dir, self.versions_dir, self.amendments_dir, self.audit_dir]:
            d.mkdir(parents=True, exist_ok=True)

    # --- Protocols ---

    def load_protocol(self, protocol_id: str) -> Optional[Dict[str, Any]]:
        path = self.base_dir / f"{protocol_id}.json"
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)

    def save_protocol(self, protocol_id: str, data: Dict[str, Any]) -> None:
        path = self.base_dir / f"{protocol_id}.json"
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    # --- Versions ---

    def load_versions(self, protocol_id: str) -> List[Dict[str, Any]]:
        path = self.versions_dir / f"{protocol_id}.json"
        if not path.exists():
            return []
        with open(path) as f:
            return json.load(f)

    def save_versions(self, protocol_id: str, versions: List[Dict[str, Any]]) -> None:
        path = self.versions_dir / f"{protocol_id}.json"
        with open(path, "w") as f:
            json.dump(versions, f, indent=2, default=str)

    # --- Amendments ---

    def load_amendments(self, protocol_id: str) -> List[Dict[str, Any]]:
        path = self.amendments_dir / f"{protocol_id}.json"
        if not path.exists():
            return []
        with open(path) as f:
            return json.load(f)

    def save_amendments(self, protocol_id: str, amendments: List[Dict[str, Any]]) -> None:
        path = self.amendments_dir / f"{protocol_id}.json"
        with open(path, "w") as f:
            json.dump(amendments, f, indent=2, default=str)

    # --- Audit Log ---

    def load_audit_log(self, protocol_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        path = self.audit_dir / f"{protocol_id}.json"
        if not path.exists():
            return []
        with open(path) as f:
            log = json.load(f)
        return list(reversed(log[-limit:]))

    def add_audit_entry(self, protocol_id: str, entry: Dict[str, Any]) -> None:
        path = self.audit_dir / f"{protocol_id}.json"
        log = []
        if path.exists():
            with open(path) as f:
                log = json.load(f)
        log.append(entry)
        with open(path, "w") as f:
            json.dump(log, f, indent=2, default=str)
