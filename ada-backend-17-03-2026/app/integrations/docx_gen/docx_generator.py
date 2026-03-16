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


def _get_flat(flat_data, *keys):
    """Try multiple flat keys, return first non-empty value."""
    for k in keys:
        v = flat_data.get(k, "")
        if v and str(v).strip() and str(v).strip() != "None":
            return str(v).strip()
    return ""


def _build_phase1_replacements(flat_data: dict, animal_type: str) -> dict:
    """Build replacements mapping actual template placeholders to extracted data.

    The Altasciences templates use coded placeholders like:
      header_StudyNumber, header_TestArticlename, 1_1_AddObjective, etc.
    This maps extracted JSON fields to those actual template codes.
    """
    g = lambda *keys: _get_flat(flat_data, *keys)
    replacements = {}

    # --- Header-level placeholders (appear in title / cover page) ---
    placeholder_map = {
        # Cover page & headers
        "header_StudyNumber": g("BasicDetails.StudyNo", "StudyNo"),
        "header_ReferenceNumber": g("BasicDetails.SponsorRefNo"),
        "header_TestArticlename": g("BasicDetails.TestArticleName", "TestMaterial.TestArticle.0.Identification"),
        "header_Animal": g("BasicDetails.TestSubject", "TestSystem.SpeciesStrain"),
        "header_routeOfAdministration": g("StudyInfo.Route", "StudyInfo.RouteDuration"),
        "header_X_Week": g("BasicDetails.TestPeriod"),
        "header_Y_Week": g("BasicDetails.RecoveryPeriod", "ExperimentalDesign.RecoveryPeriod", "StudyInfo.RecoveryPeriod"),
        "header_CompName": g("BasicDetails.SponsorName"),
        "header_StreetAddress": g("BasicDetails.SponsorAddress.Street"),
        "header_City": g("BasicDetails.SponsorAddress.City"),
        "header_Country": g("BasicDetails.SponsorAddress.Country"),

        # Section 1 — Study Information / Objective
        "1_1_AddObjective": g("StudyInfo.Objective", "BasicDetails.Objective"),
        "1_1_TestArticleNameA": g("BasicDetails.TestArticleName", "TestMaterial.TestArticle.0.Identification"),
        "1_1_TestArticleNameB": g("BasicDetails.TestArticleName", "StudyInfo.TestArticleNameWithTexicokinecCharacteristics"),
        "1_1_AddRoute": g("StudyInfo.Route"),
        "1_1_AddDuration": g("BasicDetails.TestPeriod", "StudyInfo.RouteDuration"),
        "1_1_AddRP": g("BasicDetails.RecoveryPeriod", "ExperimentalDesign.RecoveryPeriod", "StudyInfo.RecoveryPeriod"),
        "1_1_TSName": g("BasicDetails.TestSubject", "TestSystem.SpeciesStrain", "StudyInfo.TestSpeciesName"),
        "1_1_TestSpecies": g("TestSystem.SpeciesStrain", "BasicDetails.TestSubject"),
        "1_1_metabolites": g("StudyInfo.TestArticleNameWithTexicokinecCharacteristics"),

        # Section 2 — Personnel / Sponsor
        "2_1_SponsorStudyRepresentative_NAME": g("Personnel.SDS.SponsorStudyRepresentative.0.Name", "Personnel.SponsorRepresentative.Name"),
        "2_1_SponsorStudyRepresentative_Credentials": g("Personnel.SDS.SponsorStudyRepresentative.0.Credentials"),
        "2_1_SponsorStudyRepresentative_Phone": g("Personnel.SDS.SponsorStudyRepresentative.0.Phone"),
        "2_1_SponsorStudyRepresentative_Email": g("Personnel.SDS.SponsorStudyRepresentative.0.Email"),
        "2_1_SponsorStudyMonitorOrRepresentative_Name": g("Personnel.SDS.SponsorStudyRepresentative.0.Name"),
        "2_1_SponsorStudyMonitorOrRepresentative_Credentials": g("Personnel.SDS.SponsorStudyRepresentative.0.Credentials"),
        "2_1_SponsorStudyMonitorOrRepresentative_Phone": g("Personnel.SDS.SponsorStudyRepresentative.0.Phone"),
        "2_1_SponsorStudyMonitorOrRepresentative_Email": g("Personnel.SDS.SponsorStudyRepresentative.0.Email"),
        "2_1_AddressAsCitedForSponsor": g("BasicDetails.SponsorAddress.Street", "Personnel.SDS.StudyDirector.AddressOfTestingFacility"),
        "2_1_AddressCitedForSponsor": g("BasicDetails.SponsorAddress.Street"),

        # Section 4 — Test Material
        "5_4_3_TestArticleName": g("BasicDetails.TestArticleName", "TestMaterial.TestArticle.0.Identification"),
        "4_3_add_phase_sponsor": g("BasicDetails.SponsorName"),
    }

    # Add all resolved placeholders
    for placeholder, value in placeholder_map.items():
        if value:
            replacements[placeholder] = value

    # Also try legacy "natural language" patterns some templates may use
    legacy_map = {
        "add study number": g("BasicDetails.StudyNo", "StudyNo"),
        "add study title": g("BasicDetails.StudyTitle"),
        "add objective": g("StudyInfo.Objective"),
        "add Test Article name": g("BasicDetails.TestArticleName"),
        "add route": g("StudyInfo.Route"),
        "add sponsor name": g("BasicDetails.SponsorName"),
        "add Study Director name": g("Personnel.SDS.StudyDirector.Name"),
        "add SD phone": g("Personnel.SDS.StudyDirector.Phone"),
        "add SD email": g("Personnel.SDS.StudyDirector.Email"),
        "add species": g("TestSystem.SpeciesStrain"),
        "add duration": g("BasicDetails.TestPeriod"),
    }
    for placeholder, value in legacy_map.items():
        if value:
            replacements[placeholder] = value

    # Also add ${key} style replacements as fallback
    for key, value in flat_data.items():
        if value and str(value).strip() and str(value).strip() != "None":
            replacements[f"${{{key}}}"] = str(value)

    logger.info(f"Built {len(replacements)} replacements for {animal_type} template")
    return replacements
