"""MongoDB storage backend for Azure Cosmos DB (MongoDB API).

Production backend that stores all protocol data in Cosmos DB collections.
Requires: pymongo, MONGO_CONNECTION_STRING and MONGO_DB_NAME env vars.

Collections:
  - protocols: main protocol records
  - versions: version snapshots (indexed on protocol_id + version)
  - amendments: amendment records (indexed on protocol_id + amendment_number)
  - audit_log: audit trail (indexed on protocol_id + timestamp desc)
"""

import os
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services.storage import StorageBackend

logger = logging.getLogger(__name__)


class MongoStorage(StorageBackend):
    """Cosmos DB MongoDB API storage backend."""

    def __init__(self):
        try:
            import certifi
            ca_cert = certifi.where()
        except ImportError:
            ca_cert = None

        from pymongo import MongoClient
        from pymongo.errors import ServerSelectionTimeoutError

        connection_string = os.getenv("MONGO_CONNECTION_STRING", "")
        db_name = os.getenv("MONGO_DB_NAME", "ada_platform")

        if not connection_string:
            raise ValueError(
                "MONGO_CONNECTION_STRING not set. "
                "Set STORAGE_TYPE=file to use JSON file storage instead."
            )

        try:
            connect_kwargs = {
                "serverSelectionTimeoutMS": 10000,
                "retryWrites": False,  # Cosmos DB doesn't support retryWrites
            }
            if ca_cert:
                connect_kwargs["tlsCAFile"] = ca_cert

            self.client = MongoClient(connection_string, **connect_kwargs)
            # Test connection
            self.client.server_info()
            self.db = self.client[db_name]
            logger.info(f"Connected to MongoDB database: {db_name}")
            self._ensure_indexes()
        except ServerSelectionTimeoutError as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise
        except Exception as e:
            logger.error(f"MongoDB initialization failed: {e}")
            raise

    def _ensure_indexes(self):
        """Create indexes on first connection."""
        try:
            self.db.protocols.create_index("protocol_id", unique=True, sparse=True)
            self.db.versions.create_index([("protocol_id", 1), ("version", 1)])
            self.db.versions.create_index([("protocol_id", 1), ("timestamp", -1)])
            self.db.amendments.create_index([("protocol_id", 1), ("amendment_number", 1)])
            self.db.audit_log.create_index([("protocol_id", 1), ("timestamp", -1)])
            logger.info("MongoDB indexes ensured")
        except Exception as e:
            logger.warning(f"Index creation warning (non-fatal): {e}")

    def _strip_id(self, doc: Optional[dict]) -> Optional[dict]:
        """Remove MongoDB _id field from document."""
        if doc and "_id" in doc:
            doc = dict(doc)
            del doc["_id"]
        return doc

    # --- Protocols ---

    def load_protocol(self, protocol_id: str) -> Optional[Dict[str, Any]]:
        doc = self.db.protocols.find_one({"protocol_id": protocol_id})
        return self._strip_id(doc)

    def save_protocol(self, protocol_id: str, data: Dict[str, Any]) -> None:
        data = dict(data)
        data["protocol_id"] = protocol_id
        data["updated_at"] = datetime.utcnow().isoformat() + "Z"
        self.db.protocols.update_one(
            {"protocol_id": protocol_id},
            {"$set": data},
            upsert=True,
        )

    # --- Versions ---

    def load_versions(self, protocol_id: str) -> List[Dict[str, Any]]:
        cursor = self.db.versions.find(
            {"protocol_id": protocol_id}
        ).sort("timestamp", 1)  # Oldest first (matches file storage order)
        return [self._strip_id(doc) for doc in cursor]

    def save_versions(self, protocol_id: str, versions: List[Dict[str, Any]]) -> None:
        # Replace all versions for this protocol (atomic-ish via delete + insert)
        self.db.versions.delete_many({"protocol_id": protocol_id})
        if versions:
            docs = []
            for v in versions:
                doc = dict(v)
                doc["protocol_id"] = protocol_id
                docs.append(doc)
            self.db.versions.insert_many(docs)

    # --- Amendments ---

    def load_amendments(self, protocol_id: str) -> List[Dict[str, Any]]:
        cursor = self.db.amendments.find(
            {"protocol_id": protocol_id}
        ).sort("amendment_number", 1)
        return [self._strip_id(doc) for doc in cursor]

    def save_amendments(self, protocol_id: str, amendments: List[Dict[str, Any]]) -> None:
        self.db.amendments.delete_many({"protocol_id": protocol_id})
        if amendments:
            docs = []
            for a in amendments:
                doc = dict(a)
                doc["protocol_id"] = protocol_id
                docs.append(doc)
            self.db.amendments.insert_many(docs)

    # --- Audit Log ---

    def load_audit_log(self, protocol_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        cursor = self.db.audit_log.find(
            {"protocol_id": protocol_id}
        ).sort("timestamp", -1).limit(limit)
        return [self._strip_id(doc) for doc in cursor]

    def add_audit_entry(self, protocol_id: str, entry: Dict[str, Any]) -> None:
        doc = dict(entry)
        doc["protocol_id"] = protocol_id
        self.db.audit_log.insert_one(doc)

    def close(self):
        """Close the MongoDB connection."""
        if hasattr(self, "client"):
            self.client.close()
