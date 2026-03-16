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
        try:
            doc = self.db.protocols.find_one({"protocol_id": protocol_id})
            return self._strip_id(doc)
        except Exception as e:
            logger.error(f"MongoDB load_protocol failed for {protocol_id}: {e}", exc_info=True)
            return None

    def save_protocol(self, protocol_id: str, data: Dict[str, Any]) -> None:
        try:
            data = dict(data)
            data["protocol_id"] = protocol_id
            data["updated_at"] = datetime.utcnow().isoformat() + "Z"
            # Remove document_text from MongoDB storage if too large (>1MB)
            # Store it separately or truncate to prevent hitting Cosmos DB 2MB document limit
            doc_text = data.get("document_text", "")
            if isinstance(doc_text, str) and len(doc_text) > 1_000_000:
                data["document_text_truncated"] = True
                data["document_text_length"] = len(doc_text)
                data["document_text"] = doc_text[:1_000_000]  # Truncate to 1MB
                logger.warning(f"Truncated document_text for {protocol_id} from {len(doc_text)} to 1M chars for MongoDB storage")
            result = self.db.protocols.update_one(
                {"protocol_id": protocol_id},
                {"$set": data},
                upsert=True,
            )
            logger.debug(f"MongoDB save_protocol {protocol_id}: matched={result.matched_count}, modified={result.modified_count}, upserted={result.upserted_id is not None}")
        except Exception as e:
            logger.error(f"MongoDB save_protocol FAILED for {protocol_id}: {e}", exc_info=True)
            raise  # Re-raise so caller knows the save failed

    def load_all_protocols(self) -> List[Dict[str, Any]]:
        protocols = []
        try:
            cursor = self.db.protocols.find({}, {
                "protocol_id": 1, "status": 1, "species": 1, "filename": 1,
                "created_at": 1, "updated_at": 1, "extracted_json": 1
            })
            for doc in cursor:
                doc = self._strip_id(doc) or {}
                pid = doc.get("protocol_id", "")
                ej = doc.get("extracted_json", {}) or {}
                bd = ej.get("BasicDetails", {}) or {}
                si = ej.get("StudyInfo", {}) or {}
                ts = ej.get("TestSystem", {}) or {}
                study_no = bd.get("StudyNo", "") or ej.get("StudyNo", "") or pid
                sponsor = bd.get("SponsorName", "") or ""
                species = doc.get("species", "") or ts.get("SpeciesStrain", "") or ""
                title = si.get("Objective", "")
                if title and len(title) > 120:
                    title = title[:120] + "..."
                protocols.append({
                    "protocol_id": pid,
                    "study_number": study_no,
                    "title": title or doc.get("filename", "Untitled Protocol"),
                    "sponsor": sponsor,
                    "species": species,
                    "status": doc.get("status", "unknown"),
                    "filename": doc.get("filename", ""),
                    "created_at": doc.get("created_at", ""),
                    "updated_at": doc.get("updated_at", ""),
                })
        except Exception as e:
            logger.error(f"MongoDB load_all_protocols failed: {e}", exc_info=True)
        return protocols

    # --- Versions ---

    def load_versions(self, protocol_id: str) -> List[Dict[str, Any]]:
        cursor = self.db.versions.find(
            {"protocol_id": protocol_id}
        ).sort("timestamp", 1)  # Oldest first (matches file storage order)
        return [self._strip_id(doc) for doc in cursor]

    def save_versions(self, protocol_id: str, versions: List[Dict[str, Any]]) -> None:
        try:
            self.db.versions.delete_many({"protocol_id": protocol_id})
            if versions:
                docs = []
                for v in versions:
                    doc = dict(v)
                    doc["protocol_id"] = protocol_id
                    docs.append(doc)
                self.db.versions.insert_many(docs)
            logger.debug(f"MongoDB save_versions {protocol_id}: {len(versions)} version(s) saved")
        except Exception as e:
            logger.error(f"MongoDB save_versions FAILED for {protocol_id}: {e}", exc_info=True)
            raise

    # --- Amendments ---

    def load_amendments(self, protocol_id: str) -> List[Dict[str, Any]]:
        cursor = self.db.amendments.find(
            {"protocol_id": protocol_id}
        ).sort("amendment_number", 1)
        return [self._strip_id(doc) for doc in cursor]

    def save_amendments(self, protocol_id: str, amendments: List[Dict[str, Any]]) -> None:
        try:
            self.db.amendments.delete_many({"protocol_id": protocol_id})
            if amendments:
                docs = []
                for a in amendments:
                    doc = dict(a)
                    doc["protocol_id"] = protocol_id
                    docs.append(doc)
                self.db.amendments.insert_many(docs)
            logger.debug(f"MongoDB save_amendments {protocol_id}: {len(amendments)} amendment(s) saved")
        except Exception as e:
            logger.error(f"MongoDB save_amendments FAILED for {protocol_id}: {e}", exc_info=True)
            raise

    # --- Audit Log ---

    def load_audit_log(self, protocol_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        cursor = self.db.audit_log.find(
            {"protocol_id": protocol_id}
        ).sort("timestamp", -1).limit(limit)
        return [self._strip_id(doc) for doc in cursor]

    def add_audit_entry(self, protocol_id: str, entry: Dict[str, Any]) -> None:
        try:
            doc = dict(entry)
            doc["protocol_id"] = protocol_id
            self.db.audit_log.insert_one(doc)
        except Exception as e:
            logger.error(f"MongoDB add_audit_entry FAILED for {protocol_id}: {e}", exc_info=True)
            # Don't re-raise audit failures — they shouldn't block the main operation

    # --- Users ---

    def load_user(self, username: str) -> Optional[Dict[str, Any]]:
        doc = self.db.users.find_one({"username": username})
        return self._strip_id(doc)

    def save_user(self, user_data: Dict[str, Any]) -> None:
        username = user_data.get("username", "")
        if not username:
            raise ValueError("User data must include a 'username' field")
        try:
            data = dict(user_data)
            self.db.users.update_one(
                {"username": username},
                {"$set": data},
                upsert=True,
            )
            logger.debug(f"MongoDB save_user: {username}")
        except Exception as e:
            logger.error(f"MongoDB save_user FAILED for {username}: {e}", exc_info=True)
            raise

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
