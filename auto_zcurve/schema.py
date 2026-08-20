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

    required = spec.get("required", False)
    if not isinstance(required, bool):
        raise ValueError(f"{section_name}.{field_name}.required must be true or false.")

    role = str(spec.get("role") or "").strip() or None
    description = spec.get("description")
    return FieldSpec(
        type=field_type,
        description=str(description) if description is not None else None,
        required=required,
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

    roles: dict[str, str] = {}
    for field_name, spec in out.items():
        if spec.role and spec.role in roles:
            raise ValueError(
                f"{section_name}.{field_name} repeats role `{spec.role}` already used by "
                f"{section_name}.{roles[spec.role]}."
            )
        if spec.role:
            roles[spec.role] = field_name
    return out


def parse_extraction_schema(text: str, *, path: Path | None = None) -> ExtractionSchema:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - preflight catches this
        raise RuntimeError("PyYAML is required to read extraction schemas.") from exc

    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        problem = str(getattr(exc, "problem", "") or "The YAML could not be parsed.")
        if mark is not None:
            raise ValueError(
                f"Invalid YAML at line {mark.line + 1}, column {mark.column + 1}: {problem}"
            ) from exc
        raise ValueError(f"Invalid YAML: {problem}") from exc

    if not isinstance(raw, dict):
        raise ValueError("Schema file must contain a top-level mapping.")

    name = str(raw.get("name") or "zcurve_extraction")
    description = raw.get("description")
    return ExtractionSchema(
        name=name,
        description=str(description) if description is not None else None,
        meta_data=_section(raw.get("meta_data"), "meta_data", allow_empty=True),
        effects=_section(raw.get("effects"), "effects", allow_empty=False),
        path=(path or Path("extraction_schema.yml")).resolve(),
    )


def read_extraction_schema(path: Path) -> ExtractionSchema:
    if not path.exists():
        raise FileNotFoundError(f"Schema file not found: {path}")
    return parse_extraction_schema(path.read_text(encoding="utf-8"), path=path)


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


def validate_extracted_json(
    parsed: object,
    config: ExtractionSchema,
    provider_name: str = "Gemini",
) -> None:
    if not isinstance(parsed, dict):
        raise ValueError(f"{provider_name} returned JSON that is not a top-level object.")
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


def normalize_extracted_json(
    parsed: object,
    config: ExtractionSchema,
) -> object:
    """Repair lossless scalar-to-string mistakes in otherwise valid model JSON.

    Some OpenAI-compatible structured-output backends occasionally emit an identifier
    such as ``sample_id`` as a JSON number even when its schema type is ``string``.
    Turning JSON scalars into their textual representation is lossless and avoids
    discarding an entire document for that provider-side schema violation. Other type
    mismatches remain untouched so normal validation still catches ambiguous output.
    """

    def as_text(value: object) -> object:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        return value

    def normalize_section(section: object, fields: dict[str, FieldSpec]) -> None:
        if not isinstance(section, dict):
            return
        for field_name, spec in fields.items():
            value = section.get(field_name)
            if value is None:
                continue
            if spec.type == "STRING":
                section[field_name] = as_text(value)
            elif spec.type == "ARRAY" and spec.items_type == "STRING" and isinstance(value, list):
                section[field_name] = [as_text(item) for item in value]

    if not isinstance(parsed, dict):
        return parsed
    normalize_section(parsed.get("meta_data"), config.meta_data)
    effects = parsed.get("effects")
    if isinstance(effects, list):
        for effect in effects:
            normalize_section(effect, config.effects)
    return parsed
