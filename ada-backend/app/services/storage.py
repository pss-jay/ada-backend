"""Abstract storage backend for the Ada platform.

Supports pluggable backends:
  - file: JSON file storage (default, Phase 1)
  - mongo: Cosmos DB with MongoDB API (production)

Toggle via STORAGE_TYPE environment variable.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import os
import logging

logger = logging.getLogger(__name__)


class StorageBackend(ABC):
    """Abstract interface for protocol data persistence."""

    # --- Protocols ---
    @abstractmethod
    def load_protocol(self, protocol_id: str) -> Optional[Dict[str, Any]]:
        """Load a protocol record by ID. Returns None if not found."""
        pass

    @abstractmethod
    def save_protocol(self, protocol_id: str, data: Dict[str, Any]) -> None:
        """Save/update a protocol record."""
        pass

    # --- Versions ---
    @abstractmethod
    def load_versions(self, protocol_id: str) -> List[Dict[str, Any]]:
        """Load all version snapshots for a protocol."""
        pass

    @abstractmethod
    def save_versions(self, protocol_id: str, versions: List[Dict[str, Any]]) -> None:
        """Save/replace all versions for a protocol."""
        pass

    # --- Amendments ---
    @abstractmethod
    def load_amendments(self, protocol_id: str) -> List[Dict[str, Any]]:
        """Load all amendments for a protocol."""
        pass

    @abstractmethod
    def save_amendments(self, protocol_id: str, amendments: List[Dict[str, Any]]) -> None:
        """Save/replace all amendments for a protocol."""
        pass

    # --- Audit Log ---
    @abstractmethod
    def load_audit_log(self, protocol_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Load audit log entries for a protocol (most recent first)."""
        pass

    @abstractmethod
    def add_audit_entry(self, protocol_id: str, entry: Dict[str, Any]) -> None:
        """Append a single audit entry."""
        pass


# Singleton storage instance
_storage_instance: Optional[StorageBackend] = None


def get_storage_backend() -> StorageBackend:
    """Factory: return the configured storage backend (singleton)."""
    global _storage_instance
    if _storage_instance is not None:
        return _storage_instance

    storage_type = os.getenv("STORAGE_TYPE", "file").lower()

    if storage_type == "mongo":
        from app.services.mongo_storage import MongoStorage
        _storage_instance = MongoStorage()
        logger.info("Storage backend: MongoDB (Cosmos DB)")
    else:
        from app.services.file_storage import FileStorage
        _storage_instance = FileStorage()
        logger.info("Storage backend: JSON files")

    return _storage_instance


def reset_storage_backend():
    """Reset the singleton (for testing)."""
    global _storage_instance
    _storage_instance = None
