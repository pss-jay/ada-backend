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
    """Azure OpenAI client supporting both Chat Completions and Responses API."""

    def __init__(self, endpoint: str, deployment: str, api_key: str, api_version: str = "2024-10-01-preview"):
        self.endpoint = endpoint.rstrip("/")
        self.deployment = deployment
        self.api_key = api_key
        self.api_version = api_version

    def generate(self, prompt: str, temperature: float = 0.0, max_tokens: int = 16000) -> str:
        """Call Azure OpenAI. Tries Chat Completions first, falls back to Responses API."""
        # Use Chat Completions API — universally available on Azure OpenAI
        url = f"{self.endpoint}/openai/deployments/{self.deployment}/chat/completions?api-version={self.api_version}"
        headers = {
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }

        body = {
            "messages": [
                {"role": "system", "content": "You are a precise data extraction assistant. Extract structured JSON from protocol documents."},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        logger.info(f"Calling Azure OpenAI Chat Completions: {self.deployment} (api-version={self.api_version})")

        # Retry with exponential backoff for rate limits and transient errors
        max_retries = 3
        resp = None
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=body, timeout=180)

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 2 ** attempt * 5))
                    if attempt < max_retries:
                        logger.warning(f"Rate limited (429). Retrying in {retry_after}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(retry_after)
                        continue
                    resp.raise_for_status()

                if resp.status_code == 404:
                    # Deployment not found — try Responses API as fallback
                    logger.warning(f"Chat Completions returned 404, trying Responses API...")
                    return self._generate_responses_api(prompt, temperature, max_tokens)

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
        return self._extract_text(data)

    def _generate_responses_api(self, prompt: str, temperature: float, max_tokens: int) -> str:
        """Fallback: use the newer Responses API endpoint."""
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
        resp = requests.post(url, headers=headers, json=body, timeout=180)
        resp.raise_for_status()
        data = resp.json()
        return self._extract_text(data)

    def _extract_text(self, data: dict) -> str:
        """Extract text content from either Chat Completions or Responses API response."""
        text = None
        try:
            # Chat Completions format: choices[0].message.content
            choices = data.get("choices")
            if choices and isinstance(choices, list):
                c0 = choices[0]
                msg = c0.get("message")
                if msg and isinstance(msg, dict):
                    text = msg.get("content")
                if text is None:
                    text = c0.get("text")

            # Responses API format: outputs[0].content[0].text
            if text is None:
                outputs = data.get("outputs") or data.get("output")
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
                api_version=azure_config.get("api_version", "2024-10-01-preview"),
            )

    def generate_model_json(self, document_content: str, animal_type: str) -> Any:
        """Generate a model-specific JSON by creating the prompt and calling Azure."""
        prompt = self.primary.generate_prompts(document_content=document_content, animal_type=animal_type)

        if self.azure_client:
            logger.debug("Sending prompt to Azure for animal_type=%s", animal_type)
            resp_text = self.azure_client.generate(prompt)
            parsed = _parse_json_text(resp_text)
            return parsed

        # No Azure configured: cannot perform real extraction
        raise RuntimeError(
            "Azure OpenAI is not configured. Real document extraction requires Azure OpenAI credentials. "
            "Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY in your .env file."
        )

