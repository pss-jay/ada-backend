"""Reverse-parse a re-uploaded DOCX and detect changes against the original protocol.

Strategy:
  1. Flatten the original protocol JSON to dot-notation keys
  2. Extract all text from the uploaded DOCX (paragraphs + table cells)
  3. For each original value, search the DOCX text for that value
  4. If the value is found but different (surrounding context matches), record as changed
  5. Build a reconstructed JSON from extracted values
  6. Compute field-level diff between original and reconstructed

This is a best-effort parser — not every field will be recovered. The confidence
score indicates how many fields were successfully matched.
"""

import logging
import difflib
import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from docx import Document

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Flatten / unflatten helpers
# ---------------------------------------------------------------------------

def _flatten_json(data: Any, prefix: str = "") -> Dict[str, str]:
    """Flatten nested dict to dot-notation keys. Only leaf string/number values."""
    result = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                result.update(_flatten_json(v, key))
            elif isinstance(v, list):
                for i, item in enumerate(v):
                    result.update(_flatten_json(item, f"{key}[{i}]"))
            else:
                if v is not None and str(v).strip():
                    result[key] = str(v)
    return result


def _unflatten_json(flat: Dict[str, str], template: Dict) -> Dict:
    """Reconstruct nested JSON from flat keys using the original structure as guide."""
    result = copy.deepcopy(template)

    for dotkey, value in flat.items():
        parts = _split_dotkey(dotkey)
        _set_nested(result, parts, value)

    return result


def _split_dotkey(key: str) -> List[str]:
    """Split 'A.B[0].C' into ['A', 'B', '[0]', 'C']."""
    parts = []
    for segment in key.split("."):
        if "[" in segment:
            base, rest = segment.split("[", 1)
            parts.append(base)
            parts.append(f"[{rest}")  # e.g. '[0]'
        else:
            parts.append(segment)
    return parts


def _set_nested(obj: Any, parts: List[str], value: str):
    """Set a value deep in a nested dict/list using parsed key parts."""
    for i, part in enumerate(parts[:-1]):
        if part.startswith("["):
            idx = int(part.strip("[]"))
            if isinstance(obj, list):
                while len(obj) <= idx:
                    obj.append({})
                obj = obj[idx]
        else:
            if isinstance(obj, dict):
                if part not in obj:
                    # Peek next part to decide dict vs list
                    nxt = parts[i + 1] if i + 1 < len(parts) else ""
                    obj[part] = [] if nxt.startswith("[") else {}
                obj = obj[part]

    last = parts[-1]
    if last.startswith("["):
        idx = int(last.strip("[]"))
        if isinstance(obj, list):
            while len(obj) <= idx:
                obj.append(None)
            obj[idx] = value
    elif isinstance(obj, dict):
        obj[last] = value


# ---------------------------------------------------------------------------
# DOCX text extraction
# ---------------------------------------------------------------------------

def _extract_docx_text(docx_path: str) -> List[str]:
    """Return a list of non-empty text snippets from all paragraphs and table cells."""
    doc = Document(docx_path)
    texts = []

    for para in doc.paragraphs:
        t = para.text.strip()
        if t:
            texts.append(t)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    t = para.text.strip()
                    if t:
                        texts.append(t)

    return texts


# ---------------------------------------------------------------------------
# Value matching
# ---------------------------------------------------------------------------

def _find_value_in_texts(value: str, texts: List[str]) -> Optional[str]:
    """Search for `value` in the list of text snippets.

    Returns the full snippet where the value was found, or None.
    Handles cases where the DOCX value may have been edited (close match).
    """
    val_lower = value.lower().strip()
    if len(val_lower) < 3:
        return None  # skip trivially short values

    # Exact containment
    for t in texts:
        if val_lower in t.lower():
            return t

    return None


def _match_field_in_docx(
    field_key: str,
    original_value: str,
    texts: List[str],
    all_text: str,
) -> Tuple[Optional[str], str]:
    """Try to find and extract the current value for a field in the DOCX.

    Returns (extracted_value, match_type) where match_type is:
      'exact' — original value found unchanged
      'changed' — field context found but value differs
      'missing' — could not locate field
    """
    val = original_value.strip()
    if not val or len(val) < 2:
        return None, "missing"

    # Check if the exact original value exists in the full DOCX text
    if val in all_text:
        return val, "exact"

    # For longer values, try fuzzy matching against each text snippet
    if len(val) > 15:
        best_ratio = 0.0
        best_match = None
        for t in texts:
            # Only compare snippets of similar length
            if len(t) < len(val) * 0.3 or len(t) > len(val) * 5:
                continue
            ratio = difflib.SequenceMatcher(None, val.lower(), t.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = t

        if best_ratio >= 0.6 and best_match:
            # Value was likely edited — return the new value
            return best_match, "changed"

    return None, "missing"


# ---------------------------------------------------------------------------
# Main parse function
# ---------------------------------------------------------------------------

def parse_docx_to_json(
    docx_path: str,
    original_json: Dict[str, Any],
) -> Dict[str, Any]:
    """Parse a re-uploaded DOCX and reconstruct the protocol JSON.

    Returns:
      {
        "reconstructed_json": { ... },
        "confidence": float (0-1),
        "warnings": [str, ...],
        "fields_extracted": int,
        "fields_expected": int,
        "field_status": { key: "exact"|"changed"|"missing" },
      }
    """
    warnings = []
    texts = _extract_docx_text(docx_path)
    all_text = "\n".join(texts)
    flat_original = _flatten_json(original_json)

    extracted_flat = {}
    field_status = {}
    exact_count = 0
    changed_count = 0

    for key, orig_val in flat_original.items():
        extracted_val, match_type = _match_field_in_docx(key, orig_val, texts, all_text)

        if match_type == "exact":
            extracted_flat[key] = orig_val
            field_status[key] = "exact"
            exact_count += 1
        elif match_type == "changed":
            extracted_flat[key] = extracted_val
            field_status[key] = "changed"
            changed_count += 1
        else:
            # Keep original value for missing fields (assume unchanged)
            extracted_flat[key] = orig_val
            field_status[key] = "missing"

    total = len(flat_original)
    matched = exact_count + changed_count
    confidence = matched / total if total > 0 else 0.5

    if confidence < 0.5:
        warnings.append(
            f"Low parsing confidence ({confidence:.0%}): only {matched}/{total} fields matched. "
            "Changes may not be detected accurately."
        )

    # Reconstruct nested JSON
    reconstructed = _unflatten_json(extracted_flat, original_json)

    logger.info(
        f"DOCX parsed: {exact_count} exact, {changed_count} changed, "
        f"{total - matched} missing ({confidence:.0%} confidence)"
    )

    return {
        "reconstructed_json": reconstructed,
        "confidence": confidence,
        "warnings": warnings,
        "fields_extracted": matched,
        "fields_expected": total,
        "field_status": field_status,
    }


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------

def compute_upload_diff(
    original_json: Dict[str, Any],
    parsed_json: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare original protocol with parsed DOCX upload. Returns structured diff."""
    flat_orig = _flatten_json(original_json)
    flat_parsed = _flatten_json(parsed_json)

    modified = []
    added = []
    deleted = []

    for key, orig_val in flat_orig.items():
        parsed_val = flat_parsed.get(key)
        if parsed_val is None:
            deleted.append({"field": key, "value": orig_val})
        elif str(orig_val).strip() != str(parsed_val).strip():
            inline = _inline_diff(str(orig_val), str(parsed_val))
            modified.append({
                "field": key,
                "original": orig_val,
                "updated": parsed_val,
                "inline_diff": inline,
            })

    for key, parsed_val in flat_parsed.items():
        if key not in flat_orig:
            added.append({"field": key, "value": parsed_val})

    return {
        "changes": {
            "modified": modified,
            "added": added,
            "deleted": deleted,
        },
        "summary": {
            "modified_count": len(modified),
            "added_count": len(added),
            "deleted_count": len(deleted),
            "total_changes": len(modified) + len(added) + len(deleted),
        },
    }


def _inline_diff(original: str, updated: str) -> Dict[str, Any]:
    """Character-level inline diff for redline rendering."""
    sm = difflib.SequenceMatcher(None, original, updated)
    ops = sm.get_opcodes()

    segments = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            segments.append({"type": "equal", "text": original[i1:i2]})
        elif tag == "replace":
            segments.append({"type": "delete", "text": original[i1:i2]})
            segments.append({"type": "insert", "text": updated[j1:j2]})
        elif tag == "delete":
            segments.append({"type": "delete", "text": original[i1:i2]})
        elif tag == "insert":
            segments.append({"type": "insert", "text": updated[j1:j2]})

    return {"segments": segments}


# ---------------------------------------------------------------------------
# Selective merge
# ---------------------------------------------------------------------------

def selective_merge(
    original_json: Dict[str, Any],
    parsed_json: Dict[str, Any],
    accepted_fields: List[str],
) -> Dict[str, Any]:
    """Apply only the accepted field changes from parsed_json into original_json."""
    result = copy.deepcopy(original_json)
    flat_parsed = _flatten_json(parsed_json)

    for field_key in accepted_fields:
        if field_key in flat_parsed:
            parts = _split_dotkey(field_key)
            _set_nested(result, parts, flat_parsed[field_key])

    return result
