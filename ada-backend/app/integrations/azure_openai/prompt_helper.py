"""Azure OpenAI client and prompt orchestrator for Ada extraction pipeline."""

from typing import Dict, Any, List, Optional
import os
import json
import logging
import time
import requests

from app.integrations.azure_openai.primary_prompt import PrimaryPromptGenerator

logger = logging.getLogger(__name__)


class AzureOpenAIClient:
    """Lightweight Azure OpenAI Responses API client."""

    def __init__(self, endpoint: str, deployment: str, api_key: str, api_version: str = "2023-11-01-preview"):
        self.endpoint = endpoint.rstrip("/")
        self.deployment = deployment
        self.api_key = api_key
        self.api_version = api_version

    def generate(self, prompt: str, temperature: float = 0.0, max_tokens: int = 16000) -> str:
        url = f"{self.endpoint}/openai/deployments/{self.deployment}/responses?api-version={self.api_version}"
        headers = {
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }

        body = {
            "input": prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Retry with exponential backoff for rate limits and transient errors
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=body, timeout=180)

                if resp.status_code == 429:
                    # Rate limited — backoff and retry
                    retry_after = int(resp.headers.get("Retry-After", 2 ** attempt * 5))
                    if attempt < max_retries:
                        logger.warning(f"Rate limited (429). Retrying in {retry_after}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(retry_after)
                        continue
                    resp.raise_for_status()

                if resp.status_code >= 500 and attempt < max_retries:
                    wait = 2 ** attempt * 2
                    logger.warning(f"Server error {resp.status_code}. Retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                break
            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    wait = 2 ** attempt * 3
                    logger.warning(f"Request timeout. Retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait)
                    continue
                raise
            except requests.exceptions.ConnectionError:
                if attempt < max_retries:
                    wait = 2 ** attempt * 2
                    logger.warning(f"Connection error. Retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait)
                    continue
                raise

        data = resp.json()

        text = None
        try:
            outputs = data.get("outputs")
            if outputs and isinstance(outputs, list):
                first = outputs[0]
                content = first.get("content")
                if isinstance(content, list) and len(content) > 0:
                    for item in content:
                        if isinstance(item, dict) and "text" in item:
                            text = item["text"]
                            break
                        if isinstance(item, str):
                            text = item
                            break
                elif isinstance(content, str):
                    text = content

            if text is None:
                choices = data.get("choices")
                if choices and isinstance(choices, list):
                    c0 = choices[0]
                    msg = c0.get("message")
                    if msg and isinstance(msg, dict):
                        text = msg.get("content")
                    if text is None:
                        text = c0.get("text")

            if text is None:
                text = data.get("content") or data.get("text") or json.dumps(data)
        except Exception:
            logger.exception("Error parsing Azure response JSON, returning raw payload")
            return json.dumps(data)

        return text or ""


def _parse_json_text(text: str) -> Any:
    """Attempt to parse text into JSON."""
    text = text.strip()
    if not text:
        return {}

    try:
        return json.loads(text)
    except Exception:
        pass

    start_idx = None
    for i, ch in enumerate(text):
        if ch == "{" or ch == "[":
            start_idx = i
            break
    if start_idx is None:
        return text

    open_ch = text[start_idx]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    end_idx = None
    for j in range(start_idx, len(text)):
        if text[j] == open_ch:
            depth += 1
        elif text[j] == close_ch:
            depth -= 1
            if depth == 0:
                end_idx = j
                break

    if end_idx is None:
        return text

    candidate = text[start_idx : end_idx + 1]
    try:
        return json.loads(candidate)
    except Exception:
        return text


class PromptOrchestrator:
    """Orchestrates generation of species-specific prompts and Azure OpenAI calls."""

    def __init__(self, azure_config: Optional[Dict[str, str]] = None):
        self.primary = PrimaryPromptGenerator()
        self.azure_client = None
        if azure_config and azure_config.get("api_key"):
            self.azure_client = AzureOpenAIClient(
                endpoint=azure_config.get("endpoint", ""),
                deployment=azure_config.get("deployment", "gpt-4o"),
                api_key=azure_config["api_key"],
                api_version=azure_config.get("api_version", "2023-11-01-preview"),
            )

    def generate_model_json(self, document_content: str, animal_type: str) -> Any:
        """Generate a model-specific JSON by creating the prompt and calling Azure."""
        prompt = self.primary.generate_prompts(document_content=document_content, animal_type=animal_type)

        if self.azure_client:
            logger.debug("Sending prompt to Azure for animal_type=%s", animal_type)
            resp_text = self.azure_client.generate(prompt)
            parsed = _parse_json_text(resp_text)
            return parsed

        # No Azure configured: create a default skeleton using schema
        logger.info("No Azure config provided; returning schema skeleton for %s", animal_type)
        common_schema = self.primary.common_model.schema()
        skeleton = common_schema.copy()
        if animal_type in self.primary.species_models:
            species_schema = self.primary.species_models[animal_type].schema()
            skeleton.get("properties", {}).update(species_schema.get("properties", {}))

        def make_default_from_schema(schema_obj: Dict[str, Any]) -> Any:
            t = schema_obj.get("type")
            if t == "object":
                props = schema_obj.get("properties", {})
                out = {}
                for k, v in props.items():
                    out[k] = make_default_from_schema(v)
                return out
            if t == "array":
                items = schema_obj.get("items", {"type": "string"})
                return [make_default_from_schema(items)]
            return "" if t in ("string", None) else 0

        default_obj = {}
        if skeleton.get("properties"):
            for key, subschema in skeleton["properties"].items():
                default_obj[key] = make_default_from_schema(subschema)
        else:
            default_obj = skeleton

        return default_obj
