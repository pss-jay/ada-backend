"""PrimaryPromptGenerator for Altasciences Ada Protocol System.

This module generates extraction prompts for Azure OpenAI (GPT-4o) to extract
preclinical toxicology study protocol data from SOW documents and Sponsor
Questionnaires into structured JSON format.
"""

from typing import Dict, Any
import json

from app.integrations.azure_openai.schema_models import (
    CommonProtocolModel,
    RatProtocolModel,
    DogProtocolModel,
    SwineProtocolModel,
    SPECIES_MODEL_MAP,
)


class PrimaryPromptGenerator:
    """Generates species-specific prompts for preclinical toxicology data extraction."""

    def __init__(self):
        self.common_model = CommonProtocolModel
        self.species_models = {
            "rat": RatProtocolModel,
            "dog": DogProtocolModel,
            "swine": SwineProtocolModel,
        }

    def generate_prompts(self, document_content: str, animal_type: str) -> str:
        """Generate a prompt for Azure OpenAI to extract protocol data from a document."""
        if animal_type not in self.species_models:
            raise KeyError(
                f"Unsupported animal_type: {animal_type}. "
                f"Supported types: {list(self.species_models.keys())}"
            )

        species_model = self.species_models[animal_type]
        common_schema = self.common_model.schema()
        species_schema = species_model.schema()

        merged_properties = {}
        if "properties" in common_schema:
            merged_properties.update(common_schema["properties"])
        if "properties" in species_schema:
            merged_properties.update(species_schema["properties"])

        combined_schema = {
            "type": "object",
            "properties": merged_properties,
            "required": [],
        }

        schema_json = json.dumps(combined_schema, indent=2)

        prompt = self._build_extraction_prompt(
            document_content=document_content,
            animal_type=animal_type,
            schema_json=schema_json,
        )

        return prompt

    def _build_extraction_prompt(
        self, document_content: str, animal_type: str, schema_json: str
    ) -> str:
        extraction_rules = self._get_extraction_rules(animal_type)

        prompt = f"""You are an expert preclinical toxicology study protocol data extractor for Altasciences.

Your task is to carefully review the provided document (which may contain a Statement of Work (SOW) and/or a Sponsor Questionnaire) and extract all relevant preclinical toxicology study protocol data into the JSON schema format specified below.

The document contains information from both SOW documents AND Sponsor Questionnaires. Extract data from BOTH sources when available.

EXTRACTION RULES:
{extraction_rules}

TARGET JSON SCHEMA:
{schema_json}

---
DOCUMENT CONTENT:
{document_content}
---

INSTRUCTIONS:
1. Analyze the entire document carefully, looking for data that matches the JSON schema fields above
2. Extract ALL relevant fields from the document into the JSON structure
3. Return ONLY valid JSON that conforms to the schema above
4. Apply all extraction rules listed above
5. Do not include any explanatory text, markdown formatting, or JSON code blocks
6. Return the raw JSON object only

Return the extracted data as valid JSON:"""

        return prompt

    def _get_extraction_rules(self, animal_type: str) -> str:
        rules = """
1. FIELD VALUES:
   - Extract values EXACTLY as they appear in the source document
   - Preserve all text formatting, names, dates, and numbers as written
   - For empty or missing fields, use empty string ""
   - For boolean fields, use true or false based on document context

2. LIST FIELDS:
   - Include all items found in the document
   - Maintain order as presented in the source
   - Use empty array [] if no items are found

3. ROUTE AND SPECIES NAMES:
   - Extract route names AS-IS from the document
   - Extract species names AS-IS from the document
   - Preserve exact spelling and capitalization from the source

4. STUDY DESIGN TABLE EXTRACTION:
   - From the study design table, extract:
     * Group numbers (numerical identifiers for dose groups)
     * Test materials and their descriptions
     * Dose levels for each group
     * Dose routes (administration routes)
     * Animal counts per group
   - Include all dose groups found in the document
   - Preserve exact values and units as presented

5. REGULATORY COMPLIANCE:
   - Extract GLP (Good Laboratory Practice) status from the Regulatory Compliance section
   - Use "GLP" or "non-GLP" as specified in the document

6. PERSONNEL INFORMATION:
   - Extract names, credentials, titles, phone numbers, and email addresses
   - Search both SOW documents and Questionnaires for personnel entries
   - Preserve exact formatting of names and credentials

7. SCHEDULE AND MILESTONE DATES:
   - Extract all study milestone dates
   - Extract study day references
   - Preserve date formats as presented in the document

8. DOCUMENT SECTIONS:
   - Extract data from all 22 top-level protocol sections when present
   - Search both SOW and Questionnaire sections for relevant data
   - Combine information from both documents where applicable

9. SPECIAL CHARACTERS AND FORMATTING:
   - Preserve special characters, superscripts, and subscripts
   - Maintain spacing and line breaks where they provide meaning

10. CONFIDENCE AND MISSING DATA:
    - Extract all data you can identify with confidence
    - Use empty string "" for fields that cannot be located in the document
    - Do not guess or infer values not explicitly stated"""

        return rules
