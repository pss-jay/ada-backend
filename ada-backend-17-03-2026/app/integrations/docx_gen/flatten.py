"""Flatten nested JSON into dot-notation key-value pairs."""

from typing import Dict
import logging

logger = logging.getLogger(__name__)


def flatten_json(d: dict, parent_key: str = '') -> Dict[str, str]:
    """Flatten a nested JSON/dictionary into a single-level dictionary.

    Keys use dot notation and array indices:
        e.g., {"a": {"b": [1, 2]}} becomes {"a.b[0]": "1", "a.b[1]": "2"}
    """
    try:
        items = {}

        for k, v in d.items():
            new_key = f"{parent_key}.{k}" if parent_key else k

            if isinstance(v, dict):
                items.update(flatten_json(v, new_key))
            elif isinstance(v, list):
                for idx, item in enumerate(v):
                    if isinstance(item, dict):
                        items.update(flatten_json(item, f"{new_key}[{idx}]"))
                    else:
                        items[f"{new_key}[{idx}]"] = str(item)
            else:
                items[new_key] = str(v)

        return items

    except Exception as e:
        logger.error(f"Error flattening JSON at key '{parent_key}': {e}", exc_info=True)
        raise
