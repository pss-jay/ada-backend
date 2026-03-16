"""Extraction service: coordinates Azure OpenAI calls for SOW data extraction."""

import json
import time
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.config import settings
from app.integrations.azure_openai.prompt_helper import PromptOrchestrator, _parse_json_text

logger = logging.getLogger(__name__)


class ExtractionService:
    """Service for extracting structured protocol data from SOW documents."""

    def __init__(self):
        azure_config = settings.azure_config if settings.azure_configured else None
        self.orchestrator = PromptOrchestrator(azure_config=azure_config)
        self._mock_mode = not settings.azure_configured

    @property
    def is_mock_mode(self) -> bool:
        return self._mock_mode

    def extract(self, document_text: str, animal_type: str) -> Dict[str, Any]:
        """Extract protocol data from document text.

        Args:
            document_text: The full text extracted from the SOW document.
            animal_type: The species type ('rat', 'dog', 'swine').

        Returns:
            A dict containing:
                - extracted_json: The structured protocol data
                - extraction_time_ms: Time taken in milliseconds
                - mock_mode: Whether this used mock data
        """
        start = time.time()

        try:
            logger.info(f"Starting extraction for {animal_type} ({len(document_text)} chars)")

            extracted = self.orchestrator.generate_model_json(
                document_content=document_text,
                animal_type=animal_type,
            )

            elapsed_ms = int((time.time() - start) * 1000)

            # If extraction returned a string (parse error), wrap it
            if isinstance(extracted, str):
                logger.warning("Extraction returned string instead of dict, attempting parse")
                extracted = _parse_json_text(extracted)
                if isinstance(extracted, str):
                    extracted = {"_raw_response": extracted}

            logger.info(
                f"Extraction complete for {animal_type}: "
                f"{len(extracted) if isinstance(extracted, dict) else 'N/A'} top-level keys, "
                f"{elapsed_ms}ms"
            )

            return {
                "extracted_json": extracted,
                "extraction_time_ms": elapsed_ms,
                "mock_mode": self._mock_mode,
            }

        except Exception as e:
            elapsed_ms = int((time.time() - start) * 1000)
            logger.error(f"Extraction failed for {animal_type}: {e}", exc_info=True)
            raise

    def load_sample_json(self, animal_type: str) -> Optional[Dict]:
        """Load a sample JSON file for testing (if available in data directory)."""
        sample_files = {
            "rat": "response_2021.16_rat.json",
            "dog": "response_2115.01_dog.json",
            "swine": "response_2268.99_swine.json",
        }
        filename = sample_files.get(animal_type)
        if not filename:
            return None

        sample_path = settings.DATA_DIR / filename
        if sample_path.exists():
            with open(sample_path) as f:
                return json.load(f)
        return None
