#!/usr/bin/env python3
"""Migrate existing JSON file data to Azure Cosmos DB (MongoDB API).

This one-time migration script reads all protocol data from app/data/
and inserts it into the configured Cosmos DB instance.

Usage:
    # Set environment variables first:
    export MONGO_CONNECTION_STRING="mongodb://..."
    export MONGO_DB_NAME="ada_platform"

    python3 scripts/migrate_to_cosmos.py [--data-dir app/data] [--dry-run]
"""

import json
import os
import sys
import argparse
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def load_json(path: Path) -> any:
    """Load a JSON file, return empty list/dict on error."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"  WARNING: Failed to load {path}: {e}")
        return None


def migrate(data_dir: str, connection_string: str, db_name: str, dry_run: bool = False):
    """Migrate all JSON data to Cosmos DB."""
    MongoClient = None
    if not dry_run:
        from pymongo import MongoClient

    data_path = Path(data_dir)
    versions_dir = data_path / "versions"
    amendments_dir = data_path / "amendments"
    audit_dir = data_path / "audit"

    # Stats
    stats = {
        "protocols": 0,
        "versions": 0,
        "amendments": 0,
        "audit_entries": 0,
        "errors": 0,
    }

    print(f"=== Ada Platform - Cosmos DB Migration ===")
    print(f"Data directory: {data_path}")
    print(f"Database: {db_name}")
    print(f"Dry run: {dry_run}")
    print()

    if not dry_run:
        try:
            import certifi
            ca_cert = certifi.where()
        except ImportError:
            ca_cert = None

        connect_kwargs = {
            "serverSelectionTimeoutMS": 10000,
            "retryWrites": False,
        }
        if ca_cert:
            connect_kwargs["tlsCAFile"] = ca_cert

        client = MongoClient(connection_string, **connect_kwargs)
        client.server_info()  # Test connection
        db = client[db_name]
        print("Connected to Cosmos DB successfully.\n")
    else:
        db = None

    # --- Migrate protocols ---
    print("--- Protocols ---")
    protocol_files = list(data_path.glob("*.json"))
    for pf in protocol_files:
        protocol_id = pf.stem
        data = load_json(pf)
        if data is None:
            stats["errors"] += 1
            continue

        data["protocol_id"] = protocol_id
        data["migrated_at"] = datetime.utcnow().isoformat() + "Z"

        if not dry_run:
            db.protocols.update_one(
                {"protocol_id": protocol_id},
                {"$set": data},
                upsert=True,
            )
        print(f"  [OK] {protocol_id} ({pf.name})")
        stats["protocols"] += 1

    # --- Migrate versions ---
    print("\n--- Versions ---")
    if versions_dir.exists():
        for vf in versions_dir.glob("*.json"):
            protocol_id = vf.stem
            versions = load_json(vf)
            if versions is None or not isinstance(versions, list):
                stats["errors"] += 1
                continue

            for v in versions:
                v["protocol_id"] = protocol_id

            if not dry_run:
                # Clear existing and insert fresh
                db.versions.delete_many({"protocol_id": protocol_id})
                if versions:
                    db.versions.insert_many(versions)

            print(f"  [OK] {protocol_id}: {len(versions)} version(s)")
            stats["versions"] += len(versions)
    else:
        print("  (no versions directory)")

    # --- Migrate amendments ---
    print("\n--- Amendments ---")
    if amendments_dir.exists():
        for af in amendments_dir.glob("*.json"):
            protocol_id = af.stem
            amendments = load_json(af)
            if amendments is None or not isinstance(amendments, list):
                stats["errors"] += 1
                continue

            for a in amendments:
                a["protocol_id"] = protocol_id

            if not dry_run:
                db.amendments.delete_many({"protocol_id": protocol_id})
                if amendments:
                    db.amendments.insert_many(amendments)

            print(f"  [OK] {protocol_id}: {len(amendments)} amendment(s)")
            stats["amendments"] += len(amendments)
    else:
        print("  (no amendments directory)")

    # --- Migrate audit logs ---
    print("\n--- Audit Logs ---")
    if audit_dir.exists():
        for af in audit_dir.glob("*.json"):
            protocol_id = af.stem
            entries = load_json(af)
            if entries is None or not isinstance(entries, list):
                stats["errors"] += 1
                continue

            for e in entries:
                e["protocol_id"] = protocol_id

            if not dry_run:
                # Don't delete existing audit entries (append-only)
                if entries:
                    db.audit_log.insert_many(entries)

            print(f"  [OK] {protocol_id}: {len(entries)} entry/entries")
            stats["audit_entries"] += len(entries)
    else:
        print("  (no audit directory)")

    # --- Create indexes ---
    if not dry_run:
        print("\n--- Creating indexes ---")
        db.protocols.create_index("protocol_id", unique=True, sparse=True)
        db.versions.create_index([("protocol_id", 1), ("version", 1)])
        db.versions.create_index([("protocol_id", 1), ("timestamp", -1)])
        db.amendments.create_index([("protocol_id", 1), ("amendment_number", 1)])
        db.audit_log.create_index([("protocol_id", 1), ("timestamp", -1)])
        print("  Indexes created.")

    # --- Summary ---
    print(f"\n=== Migration {'Preview' if dry_run else 'Complete'} ===")
    print(f"  Protocols:     {stats['protocols']}")
    print(f"  Versions:      {stats['versions']}")
    print(f"  Amendments:    {stats['amendments']}")
    print(f"  Audit entries: {stats['audit_entries']}")
    print(f"  Errors:        {stats['errors']}")

    if not dry_run:
        # Verification
        print(f"\n--- Verification ---")
        print(f"  protocols collection: {db.protocols.count_documents({})} documents")
        print(f"  versions collection:  {db.versions.count_documents({})} documents")
        print(f"  amendments collection: {db.amendments.count_documents({})} documents")
        print(f"  audit_log collection: {db.audit_log.count_documents({})} documents")
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate Ada data to Cosmos DB")
    parser.add_argument("--data-dir", default="app/data", help="Path to JSON data directory")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to database")
    args = parser.parse_args()

    conn_str = os.getenv("MONGO_CONNECTION_STRING", "")
    db_name = os.getenv("MONGO_DB_NAME", "ada_platform")

    if not conn_str and not args.dry_run:
        print("ERROR: MONGO_CONNECTION_STRING not set.")
        print("Set it or use --dry-run to preview.")
        sys.exit(1)

    migrate(args.data_dir, conn_str, db_name, args.dry_run)
