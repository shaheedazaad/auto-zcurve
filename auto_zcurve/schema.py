from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUPPORTED_TYPES = {"STRING", "NUMBER", "INTEGER", "BOOLEAN", "ARRAY"}


@dataclass
class FieldSpec:
    type: str
    description: str | None = None
    required: bool = False
    role: str | None = None
    items_type: str | None = None


@dataclass
class ExtractionSchema:
    name: str
    description: str | None
    meta_data: dict[str, FieldSpec]
    effects: dict[str, FieldSpec]
    path: Path


def normalize_schema_type(value: object) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in SUPPORTED_TYPES:
        raise ValueError(f"Unsupported field type: {normalized}")
    return normalized


def _field_spec(field_name: str, spec: object, section_name: str) -> FieldSpec:
    if not isinstance(spec, dict):
        raise ValueError(f"{section_name}.{field_name} must be a mapping in YAML.")

    field_type = normalize_schema_type(spec.get("type"))
    items_type = None
    if field_type == "ARRAY":
        items = spec.get("items") if isinstance(spec.get("items"), dict) else {}
        items_type = normalize_schema_type(items.get("type") or spec.get("items_type"))
        if items_type == "ARRAY":
            raise ValueError(f"{section_name}.{field_name} cannot be an array of arrays.")

    role = str(spec.get("role") or "").strip() or None
    description = spec.get("description")
    return FieldSpec(
        type=field_type,
        description=str(description) if description is not None else None,
        required=bool(spec.get("required")),
        role=role,
        items_type=items_type,
    )


def _section(fields: object, section_name: str, allow_empty: bool = False) -> dict[str, FieldSpec]:
    if fields is None:
        if allow_empty:
            return {}
        raise ValueError(f"`{section_name}` must contain at least one field.")
    if not isinstance(fields, dict):
        raise ValueError(f"`{section_name}` must be a mapping.")
    if not fields and not allow_empty:
        raise ValueError(f"`{section_name}` must contain at least one field.")

    out: dict[str, FieldSpec] = {}
    for name, spec in fields.items():
        field_name = str(name or "").strip()
        if not field_name:
            raise ValueError(f"Every field in `{section_name}` must be named.")
        out[field_name] = _field_spec(field_name, spec, section_name)
    return out


def read_extraction_schema(path: Path) -> ExtractionSchema:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - preflight catches this
        raise RuntimeError("PyYAML is required to read extraction schemas.") from exc

    if not path.exists():
        raise FileNotFoundError(f"Schema file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    if not isinstance(raw, dict):
        raise ValueError("Schema file must contain a top-level mapping.")

    name = str(raw.get("name") or "zcurve_extraction")
    description = raw.get("description")
    return ExtractionSchema(
        name=name,
        description=str(description) if description is not None else None,
        meta_data=_section(raw.get("meta_data"), "meta_data", allow_empty=True),
        effects=_section(raw.get("effects"), "effects", allow_empty=False),
        path=path.resolve(),
    )


def _schema_field(spec: FieldSpec) -> dict[str, Any]:
    out: dict[str, Any] = {"type": spec.type.lower()}
    if spec.description:
        out["description"] = spec.description
    if spec.type == "ARRAY":
        out["items"] = {"type": str(spec.items_type).lower()}
    return out


def _section_schema(fields: dict[str, FieldSpec]) -> dict[str, Any] | None:
    if not fields:
        return None
    required = [name for name, spec in fields.items() if spec.required]
    out: dict[str, Any] = {
        "type": "object",
        "properties": {name: _schema_field(spec) for name, spec in fields.items()},
    }
    if required:
        out["required"] = required
    return out


def build_response_schema(config: ExtractionSchema) -> dict[str, Any]:
    meta_schema = _section_schema(config.meta_data)
    effect_schema = _section_schema(config.effects)
    if effect_schema is None:
        raise ValueError("The extraction schema must define at least one effect field.")

    properties: dict[str, Any] = {
        "effects": {
            "type": "array",
            "description": "One entry per extracted focal effect or statistical test.",
            "items": effect_schema,
        }
    }

    required = ["effects"]
    if meta_schema is not None:
        meta: dict[str, Any] = {
            "type": "object",
            "description": "Study-level metadata repeated once per source document.",
            "properties": meta_schema["properties"],
        }
        if meta_schema.get("required"):
            meta["required"] = meta_schema["required"]
        properties = {"meta_data": meta, **properties}
        required = ["meta_data", *required]

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def build_role_lookup(config: ExtractionSchema) -> dict[str, dict[str, str]]:
    def collect(fields: dict[str, FieldSpec]) -> dict[str, str]:
        return {spec.role: name for name, spec in fields.items() if spec.role}

    meta = collect(config.meta_data)
    effect = collect(config.effects)
    return {"meta": meta, "study": meta, "effect": effect}


def _validate_scalar(value: object, field_type: str, field_path: str) -> None:
    if value is None:
        return
    if field_type == "STRING" and not isinstance(value, str):
        raise ValueError(f"{field_path} must be a string.")
    if field_type == "NUMBER" and not isinstance(value, (int, float)):
        raise ValueError(f"{field_path} must be a number.")
    if field_type == "INTEGER" and not (isinstance(value, int) and not isinstance(value, bool)):
        raise ValueError(f"{field_path} must be an integer.")
    if field_type == "BOOLEAN" and not isinstance(value, bool):
        raise ValueError(f"{field_path} must be a boolean.")


def _validate_field(value: object, spec: FieldSpec, field_path: str) -> None:
    if value is None:
        return
    if spec.type == "ARRAY":
        if not isinstance(value, list):
            raise ValueError(f"{field_path} must be an array.")
        for index, item in enumerate(value, start=1):
            _validate_scalar(item, str(spec.items_type), f"{field_path}[{index}]")
        return
    _validate_scalar(value, spec.type, field_path)


def _validate_section(section: object, fields: dict[str, FieldSpec], section_name: str) -> None:
    if not isinstance(section, dict):
        raise ValueError(f"`{section_name}` must be a JSON object.")
    for field_name, spec in fields.items():
        value = section.get(field_name)
        if spec.required and value is None:
            raise ValueError(f"Missing required field `{section_name}.{field_name}`.")
        _validate_field(value, spec, f"{section_name}.{field_name}")


def validate_extracted_json(parsed: object, config: ExtractionSchema) -> None:
    if not isinstance(parsed, dict):
        raise ValueError("Gemini returned JSON that is not a top-level object.")
    if config.meta_data:
        if "meta_data" not in parsed:
            raise ValueError("Missing top-level `meta_data` object.")
        _validate_section(parsed.get("meta_data"), config.meta_data, "meta_data")
    elif parsed.get("meta_data") is not None and not isinstance(parsed.get("meta_data"), dict):
        raise ValueError("`meta_data` must be a JSON object when present.")

    effects = parsed.get("effects")
    if not isinstance(effects, list):
        raise ValueError("Missing or invalid top-level `effects` array.")
    for index, effect in enumerate(effects, start=1):
        _validate_section(effect, config.effects, f"effects[{index}]")
