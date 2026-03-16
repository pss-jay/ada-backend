"""Section-level management service for Ada protocol editing.

Manages section-level operations including:
- Section status workflows (draft, in_review, approved, locked, needs_revision)
- Edit locks with timeout and heartbeat
- Section-level comments with resolution tracking
- Reviewer assignments
- Role-based section access control
- Presence tracking and active user management
- Section completeness metrics

Storage structure:
  - app/data/sections/{protocol_id}.json - section statuses, assignments, metadata
  - app/data/locks/{protocol_id}.json - active edit locks with timeout
  - app/data/comments/{protocol_id}.json - comments with resolution state
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.storage import get_storage_backend
from app.core.config import settings

logger = logging.getLogger(__name__)

# Lock timeout in seconds (15 minutes)
LOCK_TIMEOUT_SECONDS = 900

# Protocol sections in canonical order with metadata
PROTOCOL_SECTIONS = [
    {"id": "BasicDetails", "title": "Basic Details", "description": "Study identifier, title, and fundamental information"},
    {"id": "StudyInfo", "title": "Study Information", "description": "Study type, phase, objectives, and scope"},
    {"id": "Personnel", "title": "Personnel", "description": "Study team roles and responsibilities"},
    {"id": "RegulatoryCompliance", "title": "Regulatory Compliance", "description": "Regulatory requirements and compliance strategy"},
    {"id": "QualityAssurance", "title": "Quality Assurance", "description": "QA oversight and quality control measures"},
    {"id": "TestMaterial", "title": "Test Material", "description": "Test article properties, handling, and storage"},
    {"id": "TestSystem", "title": "Test System", "description": "Animal species, strain, and selection criteria"},
    {"id": "Husbandry", "title": "Husbandry", "description": "Housing, feeding, environmental conditions"},
    {"id": "ClinicalPathology", "title": "Clinical Pathology", "description": "Clinical observation and monitoring procedures"},
    {"id": "ComputerizedSystemsandAnalyzers", "title": "Computerized Systems and Analyzers", "description": "Laboratory instruments and data systems"},
    {"id": "AnimalCareCompliance", "title": "Animal Care Compliance", "description": "IACUC compliance and animal welfare measures"},
    {"id": "Reporting", "title": "Reporting", "description": "Report format and submission requirements"},
    {"id": "ArchivalStorage", "title": "Archival Storage", "description": "Data archival and retention procedures"},
    {"id": "TissueCollectionsandPreservation", "title": "Tissue Collections and Preservation", "description": "Specimen collection, handling, and preservation"},
    {"id": "Approvals", "title": "Approvals", "description": "Sign-off and approval tracking"},
    {"id": "ExperimentalDesign", "title": "Experimental Design", "description": "Study design, randomization, and blinding"},
    {"id": "ExperimentalObservationsandProcedures", "title": "Experimental Observations and Procedures", "description": "In-life observations and procedures"},
    {"id": "LaboratoryAssessments", "title": "Laboratory Assessments", "description": "Lab parameters and clinical pathology assessments"},
    {"id": "TerminalProceduresandPathology", "title": "Terminal Procedures and Pathology", "description": "Necropsy, histopathology, and tissue analysis"},
    {"id": "DataAnalysis", "title": "Data Analysis", "description": "Statistical methods and analysis approach"},
    {"id": "StudyDesignGuidance", "title": "Study Design Guidance", "description": "Reference guidance and regulatory notes"},
]

# Role-based default section assignments
SECTION_ROLE_DEFAULTS = {
    "study_director": [s["id"] for s in PROTOCOL_SECTIONS],  # All sections
    "toxicologist": [
        "ExperimentalDesign",
        "ExperimentalObservationsandProcedures",
        "DataAnalysis",
    ],
    "pathologist": [
        "TerminalProceduresandPathology",
        "TissueCollectionsandPreservation",
    ],
    "clinical_pathologist": [
        "ClinicalPathology",
        "LaboratoryAssessments",
    ],
    "qa_manager": [
        "QualityAssurance",
        "RegulatoryCompliance",
    ],
    "sponsor": [],  # Read-only, comment-only
}

# Cross-references: which sections are affected when a section changes
SECTION_CROSS_REFERENCES = {
    "BasicDetails": ["StudyInfo", "Personnel", "Approvals"],
    "StudyInfo": ["BasicDetails", "ExperimentalDesign", "DataAnalysis"],
    "Personnel": ["BasicDetails", "RegulatoryCompliance", "QualityAssurance"],
    "RegulatoryCompliance": ["Personnel", "QualityAssurance", "AnimalCareCompliance", "Approvals"],
    "QualityAssurance": ["RegulatoryCompliance", "ComputerizedSystemsandAnalyzers", "Approvals"],
    "TestMaterial": ["ExperimentalDesign", "ExperimentalObservationsandProcedures"],
    "TestSystem": ["Husbandry", "ExperimentalDesign", "AnimalCareCompliance"],
    "Husbandry": ["TestSystem", "AnimalCareCompliance", "ClinicalPathology"],
    "ClinicalPathology": ["LaboratoryAssessments", "ExperimentalObservationsandProcedures"],
    "ComputerizedSystemsandAnalyzers": ["QualityAssurance", "LaboratoryAssessments", "DataAnalysis"],
    "AnimalCareCompliance": ["RegulatoryCompliance", "TestSystem", "Husbandry"],
    "Reporting": ["Approvals", "DataAnalysis", "TerminalProceduresandPathology"],
    "ArchivalStorage": ["Reporting", "Approvals"],
    "TissueCollectionsandPreservation": ["TerminalProceduresandPathology", "LaboratoryAssessments"],
    "Approvals": ["BasicDetails", "RegulatoryCompliance", "QualityAssurance", "Reporting"],
    "ExperimentalDesign": ["StudyInfo", "TestMaterial", "TestSystem", "DataAnalysis"],
    "ExperimentalObservationsandProcedures": ["TestMaterial", "ClinicalPathology", "TerminalProceduresandPathology"],
    "LaboratoryAssessments": ["ClinicalPathology", "ComputerizedSystemsandAnalyzers", "TerminalProceduresandPathology"],
    "TerminalProceduresandPathology": ["ExperimentalObservationsandProcedures", "TissueCollectionsandPreservation", "DataAnalysis", "Reporting"],
    "DataAnalysis": ["StudyInfo", "ExperimentalDesign", "ComputerizedSystemsandAnalyzers", "TerminalProceduresandPathology"],
    "StudyDesignGuidance": [],
}


class SectionService:
    """Manages section-level operations and workflow for protocols."""

    def __init__(self):
        self.storage_backend = get_storage_backend()
        self.sections_dir = settings.DATA_DIR / "sections"
        self.locks_dir = settings.DATA_DIR / "locks"
        self.comments_dir = settings.DATA_DIR / "comments"

        for d in [self.sections_dir, self.locks_dir, self.comments_dir]:
            d.mkdir(parents=True, exist_ok=True)

    # --- File I/O Helpers ---

    def _load_sections_metadata(self, protocol_id: str) -> Dict[str, Any]:
        """Load section metadata (statuses, assignments) for a protocol."""
        path = self.sections_dir / f"{protocol_id}.json"
        if not path.exists():
            return {"sections": self._init_section_metadata()}
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading sections metadata for {protocol_id}: {e}")
            return {"sections": self._init_section_metadata()}

    def _save_sections_metadata(self, protocol_id: str, data: Dict[str, Any]) -> None:
        """Save section metadata for a protocol."""
        path = self.sections_dir / f"{protocol_id}.json"
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except IOError as e:
            logger.error(f"Error saving sections metadata for {protocol_id}: {e}")
            raise

    def _load_locks(self, protocol_id: str) -> Dict[str, Any]:
        """Load active locks for a protocol."""
        path = self.locks_dir / f"{protocol_id}.json"
        if not path.exists():
            return {}
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading locks for {protocol_id}: {e}")
            return {}

    def _save_locks(self, protocol_id: str, locks: Dict[str, Any]) -> None:
        """Save locks for a protocol."""
        path = self.locks_dir / f"{protocol_id}.json"
        try:
            with open(path, "w") as f:
                json.dump(locks, f, indent=2, default=str)
        except IOError as e:
            logger.error(f"Error saving locks for {protocol_id}: {e}")
            raise

    def _load_comments(self, protocol_id: str) -> Dict[str, Any]:
        """Load comments for a protocol."""
        path = self.comments_dir / f"{protocol_id}.json"
        if not path.exists():
            return {"comments": []}
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading comments for {protocol_id}: {e}")
            return {"comments": []}

    def _save_comments(self, protocol_id: str, data: Dict[str, Any]) -> None:
        """Save comments for a protocol."""
        path = self.comments_dir / f"{protocol_id}.json"
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except IOError as e:
            logger.error(f"Error saving comments for {protocol_id}: {e}")
            raise

    # --- Initialization ---

    def _init_section_metadata(self) -> Dict[str, Any]:
        """Initialize section metadata structure."""
        sections = {}
        for section in PROTOCOL_SECTIONS:
            section_id = section["id"]
            sections[section_id] = {
                "id": section_id,
                "title": section["title"],
                "description": section["description"],
                "status": "draft",
                "assigned_reviewer": None,
                "reviewer_assigned_at": None,
                "last_modified": datetime.utcnow().isoformat(),
                "last_modified_by": None,
                "completeness": {"total_fields": 0, "filled_fields": 0, "percentage": 0},
                "comment_count": 0,
                "unresolved_comment_count": 0,
            }
        return sections

    # --- Section Status ---

    def get_sections_status(self, protocol_id: str) -> List[Dict[str, Any]]:
        """Get status of all sections in a protocol.

        Returns list of sections with:
          - id, title, description
          - status (draft|in_review|approved|locked|needs_revision)
          - lock state and holder
          - assigned reviewer
          - completeness stats
          - comment counts
        """
        metadata = self._load_sections_metadata(protocol_id)
        locks = self._load_locks(protocol_id)
        comments_data = self._load_comments(protocol_id)

        # Build comment index by section
        comment_counts = {}
        unresolved_counts = {}
        for comment in comments_data.get("comments", []):
            section_id = comment.get("section_id")
            if section_id:
                comment_counts[section_id] = comment_counts.get(section_id, 0) + 1
                if not comment.get("resolved"):
                    unresolved_counts[section_id] = unresolved_counts.get(section_id, 0) + 1

        sections = []
        for section_id, section_data in metadata.get("sections", {}).items():
            lock_info = locks.get(section_id)
            is_locked = lock_info is not None

            section_obj = {
                "id": section_data["id"],
                "title": section_data["title"],
                "description": section_data["description"],
                "status": section_data["status"],
                "assigned_reviewer": section_data.get("assigned_reviewer"),
                "reviewer_assigned_at": section_data.get("reviewer_assigned_at"),
                "last_modified": section_data.get("last_modified"),
                "last_modified_by": section_data.get("last_modified_by"),
                "completeness": section_data.get("completeness", {}),
                "comment_count": comment_counts.get(section_id, 0),
                "unresolved_comment_count": unresolved_counts.get(section_id, 0),
                "locked": is_locked,
                "lock_holder": lock_info.get("lock_holder") if is_locked else None,
                "locked_at": lock_info.get("locked_at") if is_locked else None,
                "lock_expires_at": lock_info.get("expires_at") if is_locked else None,
            }
            sections.append(section_obj)

        return sections

    def get_section_detail(self, protocol_id: str, section_id: str) -> Dict[str, Any]:
        """Get detailed information about a specific section.

        Returns section data including:
          - Section metadata (title, description)
          - Status and lock state
          - Field values and completeness
          - Comments thread
        """
        # Verify section exists
        if not any(s["id"] == section_id for s in PROTOCOL_SECTIONS):
            raise ValueError(f"Unknown section: {section_id}")

        metadata = self._load_sections_metadata(protocol_id)
        section_meta = metadata["sections"].get(section_id)
        if not section_meta:
            raise ValueError(f"Section {section_id} not found in protocol {protocol_id}")

        locks = self._load_locks(protocol_id)
        lock_info = locks.get(section_id)

        # Load protocol data to get field values
        protocol_data = self.storage_backend.load_protocol(protocol_id) or {}
        section_fields = protocol_data.get("extracted_json", {}).get(section_id, {})

        # Load comments for this section
        comments_data = self._load_comments(protocol_id)
        section_comments = [
            c for c in comments_data.get("comments", [])
            if c.get("section_id") == section_id
        ]

        return {
            "id": section_meta["id"],
            "title": section_meta["title"],
            "description": section_meta["description"],
            "status": section_meta["status"],
            "assigned_reviewer": section_meta.get("assigned_reviewer"),
            "reviewer_assigned_at": section_meta.get("reviewer_assigned_at"),
            "last_modified": section_meta.get("last_modified"),
            "last_modified_by": section_meta.get("last_modified_by"),
            "completeness": section_meta.get("completeness", {}),
            "fields": section_fields,
            "locked": lock_info is not None,
            "lock_holder": lock_info.get("lock_holder") if lock_info else None,
            "locked_at": lock_info.get("locked_at") if lock_info else None,
            "lock_expires_at": lock_info.get("expires_at") if lock_info else None,
            "comments": section_comments,
        }

    # --- Section Field Updates ---

    def update_section_fields(
        self,
        protocol_id: str,
        section_id: str,
        fields: Dict[str, Any],
        username: str,
    ) -> Dict[str, Any]:
        """Update fields within a section.

        Validates:
          - Lock ownership (must be held by requesting user)
          - Section exists and is not locked by someone else

        Updates:
          - Field values in protocol data
          - Section metadata (last_modified, completeness)
          - Updates related sections' completeness

        Returns updated section detail.
        """
        # Verify section exists
        if not any(s["id"] == section_id for s in PROTOCOL_SECTIONS):
            raise ValueError(f"Unknown section: {section_id}")

        locks = self._load_locks(protocol_id)
        lock_info = locks.get(section_id)

        # Check lock ownership
        if lock_info and lock_info.get("lock_holder") != username:
            raise PermissionError(
                f"Section {section_id} is locked by {lock_info.get('lock_holder')}"
            )

        # Load and update protocol data
        protocol_data = self.storage_backend.load_protocol(protocol_id) or {}
        if "extracted_json" not in protocol_data:
            protocol_data["extracted_json"] = {}
        if section_id not in protocol_data["extracted_json"]:
            protocol_data["extracted_json"][section_id] = {}

        protocol_data["extracted_json"][section_id].update(fields)
        self.storage_backend.save_protocol(protocol_id, protocol_data)

        # Update section metadata
        metadata = self._load_sections_metadata(protocol_id)
        section_meta = metadata["sections"][section_id]
        section_meta["last_modified"] = datetime.utcnow().isoformat()
        section_meta["last_modified_by"] = username

        # Recompute completeness
        section_meta["completeness"] = self._compute_section_completeness(
            protocol_data.get("extracted_json", {}), section_id
        )

        self._save_sections_metadata(protocol_id, metadata)

        logger.info(
            f"Updated section {section_id} in {protocol_id} by {username}"
        )

        return self.get_section_detail(protocol_id, section_id)

    # --- Lock Management ---

    def acquire_lock(
        self, protocol_id: str, section_id: str, username: str
    ) -> Dict[str, Any]:
        """Acquire an edit lock on a section.

        Returns:
          {
            "locked": true,
            "lock_holder": username,
            "locked_at": ISO timestamp,
            "expires_at": ISO timestamp (900s from now),
            "message": "Lock acquired successfully"
          }

        Raises error if already locked by another user.
        """
        if not any(s["id"] == section_id for s in PROTOCOL_SECTIONS):
            raise ValueError(f"Unknown section: {section_id}")

        locks = self._load_locks(protocol_id)
        lock_info = locks.get(section_id)

        # Check if already locked by someone else
        if lock_info:
            expires_at = datetime.fromisoformat(lock_info["expires_at"])
            if expires_at > datetime.utcnow():
                if lock_info["lock_holder"] != username:
                    raise PermissionError(
                        f"Section locked by {lock_info['lock_holder']} "
                        f"until {lock_info['expires_at']}"
                    )
                # Already locked by this user, refresh the lock
            else:
                # Lock expired, remove it
                del locks[section_id]

        now = datetime.utcnow()
        expires_at = now + timedelta(seconds=LOCK_TIMEOUT_SECONDS)

        locks[section_id] = {
            "lock_holder": username,
            "locked_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }

        self._save_locks(protocol_id, locks)

        logger.info(f"Lock acquired on {section_id} by {username}")

        return {
            "locked": True,
            "lock_holder": username,
            "locked_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "message": "Lock acquired successfully",
        }

    def release_lock(
        self, protocol_id: str, section_id: str, username: str
    ) -> Dict[str, str]:
        """Release an edit lock on a section.

        Only the lock holder or an admin can release.
        """
        if not any(s["id"] == section_id for s in PROTOCOL_SECTIONS):
            raise ValueError(f"Unknown section: {section_id}")

        locks = self._load_locks(protocol_id)
        lock_info = locks.get(section_id)

        if not lock_info:
            raise ValueError(f"No lock exists for {section_id}")

        if lock_info["lock_holder"] != username:
            raise PermissionError(
                f"Only {lock_info['lock_holder']} can release this lock"
            )

        del locks[section_id]
        self._save_locks(protocol_id, locks)

        logger.info(f"Lock released on {section_id} by {username}")

        return {"message": "Lock released successfully"}

    def heartbeat_lock(
        self, protocol_id: str, section_id: str, username: str
    ) -> Dict[str, Any]:
        """Extend lock timeout by refreshing the expiration time.

        Lock must be held by requesting user.
        """
        if not any(s["id"] == section_id for s in PROTOCOL_SECTIONS):
            raise ValueError(f"Unknown section: {section_id}")

        locks = self._load_locks(protocol_id)
        lock_info = locks.get(section_id)

        if not lock_info:
            raise ValueError(f"No lock exists for {section_id}")

        if lock_info["lock_holder"] != username:
            raise PermissionError(
                f"Only {lock_info['lock_holder']} can heartbeat this lock"
            )

        # Check if lock is expired
        expires_at = datetime.fromisoformat(lock_info["expires_at"])
        if expires_at <= datetime.utcnow():
            del locks[section_id]
            self._save_locks(protocol_id, locks)
            raise ValueError("Lock has expired")

        # Refresh expiration
        now = datetime.utcnow()
        new_expires_at = now + timedelta(seconds=LOCK_TIMEOUT_SECONDS)
        lock_info["expires_at"] = new_expires_at.isoformat()

        self._save_locks(protocol_id, locks)

        logger.info(f"Lock heartbeat on {section_id} by {username}")

        return {
            "locked": True,
            "lock_holder": username,
            "expires_at": new_expires_at.isoformat(),
            "message": "Lock refreshed",
        }

    def force_release_lock(
        self, protocol_id: str, section_id: str, admin_username: str
    ) -> Dict[str, str]:
        """Admin override to force-release a lock.

        Note: In a real system, this should verify admin privileges.
        """
        if not any(s["id"] == section_id for s in PROTOCOL_SECTIONS):
            raise ValueError(f"Unknown section: {section_id}")

        locks = self._load_locks(protocol_id)
        lock_info = locks.get(section_id)

        if not lock_info:
            raise ValueError(f"No lock exists for {section_id}")

        previous_holder = lock_info["lock_holder"]
        del locks[section_id]
        self._save_locks(protocol_id, locks)

        logger.warning(
            f"Lock on {section_id} force-released by admin {admin_username} "
            f"(was held by {previous_holder})"
        )

        return {
            "message": f"Lock released by admin (was held by {previous_holder})"
        }

    # --- Section Status Workflow ---

    def set_section_status(
        self,
        protocol_id: str,
        section_id: str,
        status: str,
        username: str,
    ) -> Dict[str, Any]:
        """Change section status.

        Valid statuses: draft, in_review, approved, locked, needs_revision

        Status transitions:
          - draft -> in_review: submit for review
          - in_review -> approved: reviewer approves
          - in_review -> needs_revision: reviewer requests changes
          - needs_revision -> in_review: resubmit after revision
          - * -> locked: lock for changes
        """
        valid_statuses = ["draft", "in_review", "approved", "locked", "needs_revision"]
        if status not in valid_statuses:
            raise ValueError(f"Invalid status: {status}")

        if not any(s["id"] == section_id for s in PROTOCOL_SECTIONS):
            raise ValueError(f"Unknown section: {section_id}")

        metadata = self._load_sections_metadata(protocol_id)
        section_meta = metadata["sections"][section_id]

        old_status = section_meta["status"]
        section_meta["status"] = status
        section_meta["last_modified"] = datetime.utcnow().isoformat()
        section_meta["last_modified_by"] = username

        self._save_sections_metadata(protocol_id, metadata)

        logger.info(
            f"Section {section_id} status changed from {old_status} to {status} by {username}"
        )

        return {
            "id": section_id,
            "status": status,
            "previous_status": old_status,
            "message": f"Status updated to {status}",
        }

    # --- Reviewer Assignment ---

    def assign_reviewer(
        self,
        protocol_id: str,
        section_id: str,
        reviewer: str,
        assigner: str,
    ) -> Dict[str, Any]:
        """Assign a reviewer to a section."""
        if not any(s["id"] == section_id for s in PROTOCOL_SECTIONS):
            raise ValueError(f"Unknown section: {section_id}")

        metadata = self._load_sections_metadata(protocol_id)
        section_meta = metadata["sections"][section_id]

        section_meta["assigned_reviewer"] = reviewer
        section_meta["reviewer_assigned_at"] = datetime.utcnow().isoformat()

        self._save_sections_metadata(protocol_id, metadata)

        logger.info(
            f"Reviewer {reviewer} assigned to {section_id} by {assigner}"
        )

        return {
            "section_id": section_id,
            "assigned_reviewer": reviewer,
            "reviewer_assigned_at": section_meta["reviewer_assigned_at"],
            "message": f"Reviewer {reviewer} assigned",
        }

    def bulk_assign_reviewers(
        self,
        protocol_id: str,
        assignments: List[Dict[str, str]],
        assigner: str,
    ) -> Dict[str, Any]:
        """Bulk assign reviewers to multiple sections.

        assignments: [{"section_id": "SectionName", "reviewer": "username"}, ...]

        Returns:
          {
            "assigned": [{"section_id": "...", "reviewer": "..."}],
            "failed": [{"section_id": "...", "reason": "..."}],
          }
        """
        metadata = self._load_sections_metadata(protocol_id)
        assigned = []
        failed = []

        for assignment in assignments:
            section_id = assignment.get("section_id")
            reviewer = assignment.get("reviewer")

            if not section_id or not reviewer:
                failed.append({
                    "section_id": section_id,
                    "reason": "Missing section_id or reviewer",
                })
                continue

            if not any(s["id"] == section_id for s in PROTOCOL_SECTIONS):
                failed.append({
                    "section_id": section_id,
                    "reason": "Unknown section",
                })
                continue

            section_meta = metadata["sections"][section_id]
            section_meta["assigned_reviewer"] = reviewer
            section_meta["reviewer_assigned_at"] = datetime.utcnow().isoformat()

            assigned.append({
                "section_id": section_id,
                "reviewer": reviewer,
            })

        self._save_sections_metadata(protocol_id, metadata)

        logger.info(
            f"Bulk assigned {len(assigned)} reviewers by {assigner}"
        )

        return {
            "assigned": assigned,
            "failed": failed,
            "total": len(assignments),
            "success_count": len(assigned),
        }

    # --- Comments ---

    def get_comments(
        self,
        protocol_id: str,
        section_id: str,
        status_filter: str = "all",
    ) -> List[Dict[str, Any]]:
        """Get comments for a section.

        status_filter: "all", "resolved", "unresolved"
        """
        comments_data = self._load_comments(protocol_id)
        section_comments = [
            c for c in comments_data.get("comments", [])
            if c.get("section_id") == section_id
        ]

        if status_filter == "resolved":
            section_comments = [c for c in section_comments if c.get("resolved")]
        elif status_filter == "unresolved":
            section_comments = [c for c in section_comments if not c.get("resolved")]

        return section_comments

    def add_comment(
        self,
        protocol_id: str,
        section_id: str,
        field: str,
        text: str,
        author: str,
        parent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a comment to a section field.

        If parent_id is provided, this is a reply to an existing comment.

        Returns comment object with:
          - comment_id (UUID-like string)
          - section_id, field, text, author
          - created_at, resolved, parent_id
        """
        if not any(s["id"] == section_id for s in PROTOCOL_SECTIONS):
            raise ValueError(f"Unknown section: {section_id}")

        comments_data = self._load_comments(protocol_id)

        # Generate comment ID
        comment_id = (
            f"{protocol_id}_{section_id}_{field}_{int(datetime.utcnow().timestamp() * 1000)}"
        )

        comment = {
            "comment_id": comment_id,
            "section_id": section_id,
            "field": field,
            "text": text,
            "author": author,
            "created_at": datetime.utcnow().isoformat(),
            "resolved": False,
            "resolved_by": None,
            "resolved_at": None,
            "parent_id": parent_id,
        }

        comments_data["comments"].append(comment)
        self._save_comments(protocol_id, comments_data)

        # Update comment count in metadata
        metadata = self._load_sections_metadata(protocol_id)
        section_meta = metadata["sections"][section_id]
        section_meta["comment_count"] = section_meta.get("comment_count", 0) + 1
        if not comment.get("resolved"):
            section_meta["unresolved_comment_count"] = section_meta.get("unresolved_comment_count", 0) + 1
        self._save_sections_metadata(protocol_id, metadata)

        logger.info(
            f"Comment added to {section_id}.{field} by {author}"
        )

        return comment

    def resolve_comment(
        self, protocol_id: str, comment_id: str, username: str
    ) -> Dict[str, Any]:
        """Mark a comment as resolved."""
        comments_data = self._load_comments(protocol_id)
        comment = None

        for c in comments_data.get("comments", []):
            if c.get("comment_id") == comment_id:
                comment = c
                break

        if not comment:
            raise ValueError(f"Comment {comment_id} not found")

        comment["resolved"] = True
        comment["resolved_by"] = username
        comment["resolved_at"] = datetime.utcnow().isoformat()

        self._save_comments(protocol_id, comments_data)

        # Update metadata
        section_id = comment.get("section_id")
        metadata = self._load_sections_metadata(protocol_id)
        section_meta = metadata["sections"][section_id]
        section_meta["unresolved_comment_count"] = max(
            0, section_meta.get("unresolved_comment_count", 0) - 1
        )
        self._save_sections_metadata(protocol_id, metadata)

        logger.info(f"Comment {comment_id} resolved by {username}")

        return comment

    # --- Presence & Locking ---

    def get_presence(self, protocol_id: str) -> Dict[str, Any]:
        """Get active users and their locked sections.

        Returns:
          {
            "users": [
              {
                "username": "user1",
                "locked_sections": ["SectionA", "SectionB"],
                "locked_at": ISO timestamp,
              },
              ...
            ],
            "total_locks": 5,
          }
        """
        locks = self._load_locks(protocol_id)
        user_locks = {}

        for section_id, lock_info in locks.items():
            expires_at = datetime.fromisoformat(lock_info["expires_at"])
            if expires_at <= datetime.utcnow():
                # Lock expired, skip it
                continue

            username = lock_info["lock_holder"]
            if username not in user_locks:
                user_locks[username] = {
                    "username": username,
                    "locked_sections": [],
                    "locked_at": lock_info["locked_at"],
                }
            user_locks[username]["locked_sections"].append(section_id)

        return {
            "users": list(user_locks.values()),
            "total_locks": sum(len(u["locked_sections"]) for u in user_locks.values()),
        }

    # --- Completeness Computation ---

    def _compute_section_completeness(
        self, extracted_json: Dict[str, Any], section_id: str
    ) -> Dict[str, float]:
        """Compute section completeness percentage.

        Returns:
          {
            "total_fields": int,
            "filled_fields": int,
            "percentage": float (0-100),
          }

        Note: This is a placeholder implementation. A real implementation would
        need to know the expected fields for each section type.
        """
        section_data = extracted_json.get(section_id, {})

        if not section_data:
            return {
                "total_fields": 0,
                "filled_fields": 0,
                "percentage": 0,
            }

        total_fields = len(section_data)
        filled_fields = sum(
            1 for v in section_data.values()
            if v and (not isinstance(v, str) or v.strip())
        )

        percentage = (filled_fields / total_fields * 100) if total_fields > 0 else 0

        return {
            "total_fields": total_fields,
            "filled_fields": filled_fields,
            "percentage": round(percentage, 1),
        }


# Singleton instance
_section_service_instance: Optional[SectionService] = None


def get_section_service() -> SectionService:
    """Factory: return the section service singleton."""
    global _section_service_instance
    if _section_service_instance is None:
        _section_service_instance = SectionService()
    return _section_service_instance
