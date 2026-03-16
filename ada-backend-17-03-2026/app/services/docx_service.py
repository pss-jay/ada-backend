"""DOCX generation service wrapping the protocol document generator."""

import logging
from pathlib import Path
from typing import Dict, Any

from app.core.config import settings
from app.integrations.docx_gen.docx_generator import generate_docx_from_json

logger = logging.getLogger(__name__)


class DocxService:
    """Service for generating protocol DOCX documents from extracted JSON."""

    def __init__(self):
        self.templates_dir = settings.TEMPLATES_DIR
        self.output_dir = settings.OUTPUT_DIR

    def generate(self, protocol_data: dict, animal_type: str) -> Dict[str, Any]:
        """Generate a protocol DOCX from extracted data.

        Args:
            protocol_data: The extracted protocol JSON (nested structure).
            animal_type: One of 'rat', 'dog', 'swine'.

        Returns:
            Dict with:
                - file_path: Absolute path to the generated DOCX
                - file_name: Just the filename
                - file_size: Size in bytes
        """
        logger.info(f"Generating DOCX for {animal_type}")

        output_path = generate_docx_from_json(
            data=protocol_data,
            animal_type=animal_type,
            templates_dir=self.templates_dir,
            output_dir=self.output_dir,
        )

        path = Path(output_path)
        file_size = path.stat().st_size if path.exists() else 0

        logger.info(f"Generated DOCX: {path.name} ({file_size} bytes)")

        return {
            "file_path": str(path.absolute()),
            "file_name": path.name,
            "file_size": file_size,
        }
