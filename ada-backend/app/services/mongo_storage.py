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
    """MongoDB storage backend — supports both local Docker and Azure Cosmos DB."""

    def __init__(self):
        from pymongo import MongoClient
        from pymongo.errors import ServerSelectionTimeoutError

        connection_string = os.getenv("MONGO_CONNECTION_STRING", "")
        db_name = os.getenv("MONGO_DB_NAME", "ada_platform")

        if not connection_string:
            raise ValueError(
                "MONGO_CONNECTION_STRING not set. "
                "Set STORAGE_TYPE=file to use JSON file storage instead."
            )

        # Detect if connecting to Azure Cosmos DB
        is_cosmos = "cosmos.azure.com" in connection_string

        try:
            connect_kwargs = {
                "serverSelectionTimeoutMS": 10000,
            }

            if is_cosmos:
                # Cosmos DB requires TLS and does not support retryWrites
                try:
                    import certifi
                    connect_kwargs["tlsCAFile"] = certifi.where()
                except ImportError:
                    pass
                connect_kwargs["tls"] = True
                connect_kwargs["retryWrites"] = False
                logger.info("Connecting to Azure Cosmos DB (TLS enabled)")
            else:
                # Local Docker MongoDB — no TLS needed
                logger.info("Connecting to local MongoDB (TLS disabled)")

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
            self.db.users.create_index("username", unique=True, sparse=True)
            self.db.pending_changes.create_index("protocol_id", unique=True, sparse=True)
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
        ).sort("timestamp", 1)
        return [self._strip_id(doc) for doc in cursor]

    def save_versions(self, protocol_id: str, versions: List[Dict[str, Any]]) -> None:
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

    # --- Users ---

    def load_user(self, username: str) -> Optional[Dict[str, Any]]:
        doc = self.db.users.find_one({"username": username})
        return self._strip_id(doc)

    def save_user(self, user_data: Dict[str, Any]) -> None:
        username = user_data.get("username", "")
        if not username:
            raise ValueError("User data must include a 'username' field")
        data = dict(user_data)
        self.db.users.update_one(
            {"username": username},
            {"$set": data},
            upsert=True,
        )

    def load_all_users(self) -> List[Dict[str, Any]]:
        cursor = self.db.users.find().sort("username", 1)
        return [self._strip_id(doc) for doc in cursor]

    # --- Pending Changes ---

    def load_pending_changes(self, protocol_id: str) -> Optional[Dict[str, Any]]:
        doc = self.db.pending_changes.find_one({"protocol_id": protocol_id})
        return self._strip_id(doc)

    def save_pending_changes(self, protocol_id: str, changes: Dict[str, Any]) -> None:
        data = dict(changes)
        data["protocol_id"] = protocol_id
        self.db.pending_changes.update_one(
            {"protocol_id": protocol_id},
            {"$set": data},
            upsert=True,
        )

    def delete_pending_changes(self, protocol_id: str) -> None:
        self.db.pending_changes.delete_one({"protocol_id": protocol_id})

    def close(self):
        """Close the MongoDB connection."""
        if hasattr(self, "client"):
            self.client.close()
