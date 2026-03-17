from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import os
import logging

logger = logging.getLogger(__name__)


class StorageBackend(ABC):

    @abstractmethod
    def load_protocol(self, protocol_id: str) -> Optional[Dict[str, Any]]: pass

    @abstractmethod
    def save_protocol(self, protocol_id: str, data: Dict[str, Any]) -> None: pass

    @abstractmethod
    def load_all_protocols(self) -> List[Dict[str, Any]]: pass

    @abstractmethod
    def load_versions(self, protocol_id: str) -> List[Dict[str, Any]]: pass

    @abstractmethod
    def save_versions(self, protocol_id: str, versions: List[Dict[str, Any]]) -> None: pass

    @abstractmethod
    def load_amendments(self, protocol_id: str) -> List[Dict[str, Any]]: pass

    @abstractmethod
    def save_amendments(self, protocol_id: str, amendments: List[Dict[str, Any]]) -> None: pass

    @abstractmethod
    def load_audit_log(self, protocol_id: str, limit: int = 50) -> List[Dict[str, Any]]: pass

    @abstractmethod
    def add_audit_entry(self, protocol_id: str, entry: Dict[str, Any]) -> None: pass

    @abstractmethod
    def load_user(self, username: str) -> Optional[Dict[str, Any]]: pass

    @abstractmethod
    def save_user(self, user_data: Dict[str, Any]) -> None: pass

    @abstractmethod
    def load_all_users(self) -> List[Dict[str, Any]]: pass

    @abstractmethod
    def load_pending_changes(self, protocol_id: str) -> Optional[Dict[str, Any]]: pass

    @abstractmethod
    def save_pending_changes(self, protocol_id: str, changes: Dict[str, Any]) -> None: pass

    @abstractmethod
    def delete_pending_changes(self, protocol_id: str) -> None: pass


_storage_instance: Optional[StorageBackend] = None


def get_storage_backend() -> StorageBackend:
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
    global _storage_instance
    _storage_instance = None
