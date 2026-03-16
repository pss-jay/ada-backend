"""Minimal pydantic shim for environments where pydantic is not installable.

Provides BaseModel and Field with just enough functionality
to support schema generation (.schema()) used by the extraction pipeline.
"""

import json
import typing
from typing import Any, Dict, List, Optional


def Field(default=None, default_factory=None, **kwargs):
    """Stub for pydantic.Field."""
    if default_factory is not None:
        return default_factory()
    return default


class _ModelMeta(type):
    """Metaclass that enables schema() on model classes."""

    def schema(cls):
        """Generate a JSON-schema-like dict from type annotations."""
        # Use raw __annotations__ (strings) rather than get_type_hints
        # because __future__.annotations makes everything a string
        all_annotations = {}
        for klass in reversed(cls.__mro__):
            if hasattr(klass, "__annotations__"):
                all_annotations.update(klass.__annotations__)

        properties = {}
        for name, type_str in all_annotations.items():
            if name.startswith("_"):
                continue
            properties[name] = _annotation_to_schema(type_str, cls)

        return {
            "title": cls.__name__,
            "type": "object",
            "properties": properties,
        }


class BaseModel(metaclass=_ModelMeta):
    """Minimal BaseModel shim supporting schema generation and basic instantiation."""

    class Config:
        extra = "allow"

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        all_annotations = {}
        for klass in reversed(type(self).__mro__):
            if hasattr(klass, "__annotations__"):
                all_annotations.update(klass.__annotations__)
        for name in all_annotations:
            if not hasattr(self, name):
                default = getattr(type(self), name, None)
                setattr(self, name, default)

    def dict(self):
        result = {}
        all_annotations = {}
        for klass in reversed(type(self).__mro__):
            if hasattr(klass, "__annotations__"):
                all_annotations.update(klass.__annotations__)
        for name in all_annotations:
            if name.startswith("_"):
                continue
            val = getattr(self, name, None)
            if isinstance(val, BaseModel):
                result[name] = val.dict()
            else:
                result[name] = val
        return result

    @classmethod
    def schema(cls):
        return type(cls).schema(cls)

    @classmethod
    def parse_obj(cls, data):
        if isinstance(data, dict):
            return cls(**data)
        return cls()


def _annotation_to_schema(ann, context_cls=None) -> dict:
    """Convert a type annotation (possibly a string) to a JSON schema dict."""
    if isinstance(ann, str):
        return _str_annotation_to_schema(ann, context_cls)
    return _type_to_schema(ann, context_cls)


def _str_annotation_to_schema(s: str, context_cls=None) -> dict:
    """Parse a string annotation like 'Optional[List[str]]' into schema."""
    s = s.strip()

    # Optional[X]
    if s.startswith("Optional[") and s.endswith("]"):
        inner = s[9:-1]
        return _str_annotation_to_schema(inner, context_cls)

    # List[X]
    if s.startswith("List[") and s.endswith("]"):
        inner = s[5:-1]
        return {"type": "array", "items": _str_annotation_to_schema(inner, context_cls)}

    # Dict[X, Y]
    if s.startswith("Dict[") and s.endswith("]"):
        return {"type": "object"}

    # Primitive types
    type_map = {
        "str": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
        "Any": "string",
    }
    if s in type_map:
        return {"type": type_map[s]}

    # Could be a model class name — look it up in context
    if context_cls:
        # Search in the module where context_cls is defined
        import sys
        module = sys.modules.get(context_cls.__module__)
        if module:
            ref_cls = getattr(module, s, None)
            if ref_cls and isinstance(ref_cls, type) and issubclass(ref_cls, BaseModel):
                return type(ref_cls).schema(ref_cls)

    # Default to object
    return {"type": "object"}


def _type_to_schema(type_hint, context_cls=None) -> dict:
    """Convert a resolved Python type hint to a JSON schema."""
    origin = getattr(type_hint, "__origin__", None)
    args = getattr(type_hint, "__args__", ())

    if origin is type(None):
        return {"type": "null"}

    # Union (Optional)
    if origin is getattr(typing, "Union", None):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _type_to_schema(non_none[0], context_cls)
        return {"type": "string"}

    if origin is list:
        item_schema = _type_to_schema(args[0], context_cls) if args else {"type": "string"}
        return {"type": "array", "items": item_schema}

    if origin is dict:
        return {"type": "object"}

    if isinstance(type_hint, type) and issubclass(type_hint, BaseModel):
        return type(type_hint).schema(type_hint)

    type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}
    if type_hint in type_map:
        return {"type": type_map[type_hint]}

    return {"type": "string"}
