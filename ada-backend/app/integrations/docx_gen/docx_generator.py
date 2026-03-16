"""Simplified DOCX generator for Phase 1.

This uses basic placeholder replacement. The full conditional handlers
and species-specific mappers from the original 431-file codebase will
be integrated in Phase 2.
"""

import json
import re
from pathlib import Path
from datetime import datetime
from docx import Document
import logging

from app.integrations.docx_gen.flatten import flatten_json
from app.integrations.docx_gen.animal_detector import AnimalDetectorService

logger = logging.getLogger(__name__)


def _robust_replace(paragraph, replacements: dict):
    """Replace placeholders in a paragraph while preserving formatting.

    Handles cases where Word splits placeholder text across multiple runs.
    """
    full_text = paragraph.text
    replaced = False

    for key, val in replacements.items():
        if key in full_text:
            full_text = full_text.replace(key, str(val))
            replaced = True

    if replaced and paragraph.runs:
        # Preserve the formatting of the first run
        for i, run in enumerate(paragraph.runs):
            if i == 0:
                run.text = full_text
            else:
                run.text = ""


def generate_docx_from_json(data: dict, animal_type: str, templates_dir: Path, output_dir: Path) -> str:
    """Generate a protocol DOCX from extracted JSON data.

    Args:
        data: The extracted protocol JSON (nested structure).
        animal_type: One of 'rat', 'dog', 'swine'.
        templates_dir: Path to the templates directory.
        output_dir: Path to save the generated DOCX.

    Returns:
        The path to the generated DOCX file.
    """
    try:
        logger.info(f"=== Starting DOCX generation for {animal_type} ===")

        template_map = {
            "rat": "page_template_rodent.docx",
            "dog": "page_template_dog.docx",
            "swine": "page_template_swine.docx",
            "common": "page_template_rodent.docx",
        }

        if animal_type not in template_map:
            raise ValueError(f"No template defined for animal type: {animal_type}")

        template_path = templates_dir / template_map[animal_type]
        logger.info(f"Using template: {template_path}")

        if not template_path.exists():
            raise FileNotFoundError(f"Template file not found: {template_path}")

        # Flatten JSON
        flat_data = flatten_json(data)
        logger.info(f"Flattened data: {len(flat_data)} fields")

        study_no = flat_data.get("BasicDetails.StudyNo", "unknown")

        # Phase 1: Build basic replacements from flat data
        # Map common flat keys to template placeholder patterns
        replacements = _build_phase1_replacements(flat_data, animal_type)

        # Load document
        doc = Document(str(template_path))

        # Replace in paragraphs
        for para in doc.paragraphs:
            _robust_replace(para, replacements)

        # Replace in tables
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        _robust_replace(para, replacements)

        # Replace in headers/footers
        for section in doc.sections:
            if section.header:
                for para in section.header.paragraphs:
                    _robust_replace(para, replacements)
            if section.footer:
                for para in section.footer.paragraphs:
                    _robust_replace(para, replacements)

        # Save document
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_file = output_dir / f"protocol_{study_no}_{animal_type}_{timestamp}.docx"
        doc.save(str(output_file))

        logger.info(f"DOCX saved successfully at: {output_file}")
        return str(output_file)

    except Exception as e:
        logger.error("Unexpected error during DOCX generation", exc_info=True)
        raise


def _build_phase1_replacements(flat_data: dict, animal_type: str) -> dict:
    """Build a basic replacements dictionary for Phase 1.

    Maps flattened JSON keys to template placeholder patterns used in the
    Altasciences protocol templates. The templates use natural language
    placeholders like "add objective", "add Test Article name", etc.

    Phase 2 will use the full species-specific mapper functions from the
    original codebase for complete coverage.
    """
    replacements = {}

    # Common placeholder mappings (natural language → flat JSON key)
    placeholder_map = {
        # Basic Details
        "add study number": flat_data.get("BasicDetails.StudyNo", ""),
        "add study title": flat_data.get("BasicDetails.StudyTitle", ""),
        "add objective": flat_data.get("BasicDetails.Objective", ""),
        "add Test Article name": flat_data.get("TestMaterial.TestArticleName", ""),
        "add route": flat_data.get("TestMaterial.RouteOfAdministration", ""),
        "add sponsor name": flat_data.get("BasicDetails.SponsorName", ""),

        # Personnel
        "add Study Director name": flat_data.get("Personnel.StudyDirector.Name", ""),
        "add SD phone": flat_data.get("Personnel.StudyDirector.Phone", ""),
        "add SD email": flat_data.get("Personnel.StudyDirector.Email", ""),
        "add Sponsor Representative name": flat_data.get("Personnel.SponsorRepresentative.Name", ""),

        # Study Info
        "add species": flat_data.get("StudyInfo.Species", ""),
        "add strain": flat_data.get("StudyInfo.Strain", ""),
        "add duration": flat_data.get("StudyInfo.Duration", ""),
        "add dose levels": flat_data.get("StudyInfo.DoseLevels", ""),

        # Regulatory
        "add GLP status": flat_data.get("RegulatoryCompliance.GLPCompliance", ""),

        # Schedule
        "add initiation date": flat_data.get("Schedule.AnticipatedStartDate", ""),
    }

    # Add all resolved placeholders
    for placeholder, value in placeholder_map.items():
        if value:
            replacements[placeholder] = value

    # Also add the raw flat_data keys as replacements (some templates use these)
    for key, value in flat_data.items():
        if value and value not in ("None", ""):
            replacements[f"${{{key}}}"] = value

    return replacements
