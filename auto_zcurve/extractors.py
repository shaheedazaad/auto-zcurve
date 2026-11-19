"""Extractors: named, saved configurations a run uses to reach a model.

Definitions live in `extractors.json` next to the app settings and never
contain an API key. Keys live in the credential-store vault
(`credentials.KeyVault`), keyed by extractor id.
"""

from __future__ import annotations

import functools
import json
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .config import (
    REASONING_EFFORT_LABELS,
    AppSettings,
    RunSettings,
    app_settings_path,
    load_app_settings,
    load_run_settings,
    load_selected_extractor_id,
    save_app_settings,
    save_selected_extractor_id,
    SERVICE_TIER_LABELS,
    normalize_endpoint_url,
    normalize_openrouter_provider,
    normalize_reasoning_effort,
    normalize_response_format,
    normalize_service_tier,
)
from .credentials import LEGACY_CREDENTIAL_ITEMS, credentials_dir, legacy_preferences_path
from .models import list_live_models, normalize_model_name, validate_model_option
from .providers import normalize_provider, provider_label


RESPONSE_FORMAT_LABELS = {
    "json_schema": "Strict JSON schema",
    "json_object": "JSON object",
    "none": "Prompt only",
}
COMMON_FIELDS = (
    "name", "provider", "model", "max_output_tokens", "request_timeout_sec", "parallel_requests", "request_delay_sec",
)
MAX_OUTPUT_TOKENS_LIMIT = 100_000_000
# What an empty maximum output means for each provider.
DEFAULT_OUTPUT_LABELS = {
    "gemini": "Model maximum",
    "openrouter": "Endpoint maximum",
    "openai_compatible": "Server default",
}
PROVIDER_FIELDS = {
    "gemini": ("service_tier",),
    "openrouter": ("reasoning_effort", "openrouter_provider", "openrouter_allow_fallbacks"),
    "openai_compatible": ("base_url", "response_format", "context_length"),
}
NAME_MAX_LENGTH = 60
LEGACY_DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
LEGACY_DEFAULT_SERVICE_TIER = "flex"
# Saved-key flags in the old preferences.json, by provider.
LEGACY_SAVED_KEY_FLAGS = {
    "gemini": "saved_api_key_configured",
    "openrouter": "openrouter_saved_api_key_configured",
    "openai_compatible": "openai_compatible_saved_api_key_configured",
}


class ExtractorError(ValueError):
    """An extractor definition is invalid or cannot be found."""


class ExtractorsUnreadable(RuntimeError):
    """The extractors file exists but can't be read, so nothing may overwrite it."""


@dataclass(frozen=True)
class Extractor:
    id: str
    name: str
    provider: str
    model: str
    request_timeout_sec: int = 600
    parallel_requests: int = 1
    request_delay_sec: int = 0
    # None uses the provider's own limit; see DEFAULT_OUTPUT_LABELS.
    max_output_tokens: int | None = None
    service_tier: str = "flex"
    reasoning_effort: str = "high"
    openrouter_provider: str = ""
    openrouter_allow_fallbacks: bool = True
    base_url: str = ""
    response_format: str = "json_schema"
    context_length: int | None = None
    key_saved: bool = False
    # Set only on extractors created by migration until their old key is moved.
    legacy_credential: str = ""

    @property
    def provider_label(self) -> str:
        return provider_label(self.provider)

    @property
    def requires_key(self) -> bool:
        return self.provider != "openai_compatible"

    @property
    def has_saved_key(self) -> bool:
        """Whether the key vault holds a key for this extractor (or will, once its old key moves)."""
        return self.key_saved or bool(self.legacy_credential)

    def applicable_fields(self) -> tuple[str, ...]:
        return COMMON_FIELDS + PROVIDER_FIELDS[self.provider]

    def snapshot(self) -> dict[str, object]:
        """Every non-secret setting a run used, for provenance."""
        values = asdict(self)
        return {name: values[name] for name in self.applicable_fields()}

    def summary(self) -> list[tuple[str, str]]:
        """Label/value rows describing the settings, for display."""
        rows = [("Provider", self.provider_label), ("Model", self.model or "Not set")]
        if self.provider == "gemini":
            rows.append(("Service tier", SERVICE_TIER_LABELS[self.service_tier]))
        if self.provider == "openrouter":
            rows.append(("Reasoning effort", REASONING_EFFORT_LABELS[self.reasoning_effort]))
            rows.append(("Route to provider", self.openrouter_provider or "Automatic"))
            rows.append(("Fallbacks", "Allowed" if self.openrouter_allow_fallbacks else "Not allowed"))
        if self.provider == "openai_compatible":
            rows.append(("API base URL", self.base_url or "Not set"))
            rows.append(("JSON output mode", RESPONSE_FORMAT_LABELS[self.response_format]))
            if self.context_length:
                rows.append(("Context length", f"{self.context_length:,} tokens"))
        rows.append((
            "Maximum output",
            f"{self.max_output_tokens:,} tokens" if self.max_output_tokens else DEFAULT_OUTPUT_LABELS[self.provider],
        ))
        rows.append(("Parallel articles", str(self.parallel_requests)))
        rows.append(("Delay between requests", f"{self.request_delay_sec} s"))
        rows.append(("Request timeout", f"{self.request_timeout_sec} s"))
        return rows


# Every read-modify-write of the extractors file holds this lock, so requests
# handled on worker threads can't overwrite each other's changes. It is
# reentrant because mutations call load_extractors, which may migrate.
_LOCK = threading.RLock()


def _serialized(function):
    @functools.wraps(function)
    def locked(*args, **kwargs):
        with _LOCK:
            return function(*args, **kwargs)
    return locked


def extractors_path() -> Path:
    return credentials_dir() / "extractors.json"


def _int_field(values: Mapping[str, object], name: str, label: str, low: int, high: int, default: int) -> int:
    raw = values.get(name)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    try:
        number = int(str(raw).strip())
    except ValueError:
        raise ExtractorError(f"{label} must be a whole number.") from None
    if not low <= number <= high:
        raise ExtractorError(f"{label} must be between {low} and {high}.")
    return number


def _bool_field(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "allow"}


def _build(values: Mapping[str, object], *, extractor_id: str, require_model: bool = True) -> Extractor:
    """Validate raw values into an extractor, keeping only its provider's fields.

    `require_model=False` accepts a saved or migrated extractor that still
    lacks its model or, for OpenAI-compatible, its base URL; it can't run
    until they are set.
    """
    try:
        provider = normalize_provider(str(values.get("provider") or ""))
    except ValueError as exc:
        raise ExtractorError(str(exc)) from None
    name = " ".join(str(values.get("name") or "").split())
    if not name:
        raise ExtractorError("Give the extractor a name.")
    if len(name) > NAME_MAX_LENGTH:
        raise ExtractorError(f"Extractor names can be at most {NAME_MAX_LENGTH} characters.")
    model_text = str(values.get("model") or "").strip()
    try:
        model = normalize_model_name(model_text, provider) if model_text or require_model else ""
    except ValueError as exc:
        raise ExtractorError(str(exc)) from None

    defaults = Extractor(id=extractor_id, name=name, provider=provider, model=model)
    extractor = replace(
        defaults,
        request_timeout_sec=_int_field(values, "request_timeout_sec", "Request timeout", 30, 3600, defaults.request_timeout_sec),
        parallel_requests=_int_field(values, "parallel_requests", "Parallel articles", 1, 32, defaults.parallel_requests),
        request_delay_sec=_int_field(values, "request_delay_sec", "Delay between requests", 0, 3600, defaults.request_delay_sec),
        max_output_tokens=_int_field(
            values, "max_output_tokens", "Maximum output", 1, MAX_OUTPUT_TOKENS_LIMIT, 0
        ) or None,
        key_saved=_bool_field(values.get("key_saved"), False),
        legacy_credential=str(values.get("legacy_credential") or ""),
    )
    try:
        if provider == "gemini":
            extractor = replace(extractor, service_tier=normalize_service_tier(values.get("service_tier")))
        elif provider == "openrouter":
            extractor = replace(
                extractor,
                reasoning_effort=normalize_reasoning_effort(values.get("reasoning_effort")),
                openrouter_provider=normalize_openrouter_provider(values.get("openrouter_provider")),
                openrouter_allow_fallbacks=_bool_field(values.get("openrouter_allow_fallbacks"), True),
            )
        else:
            base_url = normalize_endpoint_url(str(values.get("base_url") or ""))
            if not base_url and require_model:
                raise ExtractorError("Enter the API base URL of your OpenAI-compatible server.")
            context = values.get("context_length")
            extractor = replace(
                extractor,
                base_url=base_url,
                response_format=normalize_response_format(str(values.get("response_format") or "json_schema")),
                context_length=(
                    _int_field(values, "context_length", "Context length", 1, 100_000_000, 0) or None
                    if context not in (None, "") else None
                ),
            )
    except ExtractorError:
        raise
    except ValueError as exc:
        raise ExtractorError(str(exc)) from None
    return extractor


def _stored(extractor: Extractor) -> dict[str, object]:
    record: dict[str, object] = {"id": extractor.id, **extractor.snapshot()}
    if extractor.key_saved:
        record["key_saved"] = True
    if extractor.legacy_credential:
        record["legacy_credential"] = extractor.legacy_credential
    return record


@_serialized
def save_extractors(extractors: list[Extractor]) -> None:
    """Write every extractor. Records that can't be loaded are written back unchanged."""
    path = extractors_path()
    kept = [
        record for record in (_read_records(path)[1] if path.is_file() else [])
        if record.get("id") not in {item.id for item in extractors}
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique name, so a writer outside this lock can't share the temporary file.
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps({"version": 1, "extractors": [*(_stored(item) for item in extractors), *kept]}, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_records(path: Path) -> tuple[list[Extractor], list[dict]]:
    """The extractors in the file, and the records that couldn't be loaded.

    Raises ExtractorsUnreadable when the file can't be read or isn't an
    extractors file, so a later save never replaces what couldn't be read.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExtractorsUnreadable(
            f"Couldn't read {path}: {exc}. Fix or move the file, then try again."
        ) from exc
    records = payload.get("extractors") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ExtractorsUnreadable(f"{path} has no extractor list. Fix or move the file, then try again.")
    extractors: list[Extractor] = []
    unloadable: list[dict] = []
    for record in records:
        if not isinstance(record, dict) or not record.get("id"):
            continue
        try:
            extractors.append(_build(record, extractor_id=str(record["id"]), require_model=False))
        except ExtractorError:
            # Kept in the file (see save_extractors), so it isn't lost.
            unloadable.append(record)
    return extractors, unloadable


@_serialized
def load_extractors() -> list[Extractor]:
    """Return every loadable extractor in creation order."""
    path = extractors_path()
    if not path.is_file():
        return _migrate_legacy_settings()
    return _read_records(path)[0]


def _check_unique_name(extractors: list[Extractor], name: str, extractor_id: str) -> None:
    if any(item.name.casefold() == name.casefold() and item.id != extractor_id for item in extractors):
        raise ExtractorError(f"An extractor called “{name}” already exists.")


def create_extractor(
    values: Mapping[str, object],
    *,
    check: Callable[[Extractor], None] | None = None,
) -> Extractor:
    """Validate and save a new extractor. `check` can refuse it before it is saved.

    `check` may call the provider, so it runs without the lock; the name is
    checked again under the lock before saving.
    """
    extractor = _build({**values, "key_saved": False, "legacy_credential": ""}, extractor_id=str(uuid.uuid4()))
    _check_unique_name(load_extractors(), extractor.name, extractor.id)
    if check is not None:
        check(extractor)
    with _LOCK:
        extractors = load_extractors()
        _check_unique_name(extractors, extractor.name, extractor.id)
        save_extractors([*extractors, extractor])
    return extractor


def get_extractor(extractor_id: str) -> Extractor | None:
    return next((item for item in load_extractors() if item.id == extractor_id), None)


def find_extractor_by_name(name: str) -> Extractor | None:
    wanted = " ".join(name.split()).casefold()
    return next((item for item in load_extractors() if item.name.casefold() == wanted), None)


def _index(extractors: list[Extractor], extractor_id: str) -> int:
    for index, item in enumerate(extractors):
        if item.id == extractor_id:
            return index
    raise ExtractorError("That extractor no longer exists.")


def update_extractor(
    extractor_id: str,
    values: Mapping[str, object],
    *,
    check: Callable[[Extractor, Extractor], None] | None = None,
) -> Extractor:
    """Replace an extractor's settings. Its provider and key status never change here.

    `check(current, updated)` can refuse the change before it is saved. It may
    call the provider, so it runs without the lock; the change is then applied
    under the lock to the extractor as it is by then.
    """
    def build(current: Extractor) -> Extractor:
        return _build(
            {
                **values,
                "provider": current.provider,
                "key_saved": current.key_saved,
                "legacy_credential": current.legacy_credential,
            },
            extractor_id=current.id,
        )

    extractors = load_extractors()
    current = extractors[_index(extractors, extractor_id)]
    updated = build(current)
    _check_unique_name(extractors, updated.name, updated.id)
    if check is not None:
        check(current, updated)
    with _LOCK:
        extractors = load_extractors()
        index = _index(extractors, extractor_id)
        # Key status may have changed while the check ran; keep the latest.
        updated = build(extractors[index])
        _check_unique_name(extractors, updated.name, updated.id)
        extractors[index] = updated
        save_extractors(extractors)
    return updated


@_serialized
def duplicate_extractor(extractor_id: str) -> Extractor:
    """Copy every setting except the key, under a new unused name."""
    extractors = load_extractors()
    original = extractors[_index(extractors, extractor_id)]
    taken = {item.name.casefold() for item in extractors}
    counter = 1
    while True:
        suffix = " copy" if counter == 1 else f" copy {counter}"
        # Shorten the original name so the suffix always fits the limit.
        name = original.name[:NAME_MAX_LENGTH - len(suffix)].rstrip() + suffix
        if name.casefold() not in taken:
            break
        counter += 1
    copy = replace(original, id=str(uuid.uuid4()), name=name, key_saved=False, legacy_credential="")
    save_extractors([*extractors, copy])
    return copy


@_serialized
def delete_extractor(extractor_id: str) -> Extractor:
    extractors = load_extractors()
    removed = extractors.pop(_index(extractors, extractor_id))
    save_extractors(extractors)
    return removed


@_serialized
def set_key_saved(extractor_id: str, saved: bool) -> None:
    """Record whether the vault holds this extractor's key; a removal also drops any old key claim."""
    extractors = load_extractors()
    index = _index(extractors, extractor_id)
    changes: dict[str, object] = {"key_saved": saved}
    if not saved:
        changes["legacy_credential"] = ""
    extractors[index] = replace(extractors[index], **changes)
    save_extractors(extractors)


def _read_json_object(path: Path) -> dict | None:
    """The file's JSON object: {} when it doesn't exist, None when it can't be read."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _migrate_legacy_settings() -> list[Extractor]:
    """Turn the provider settings of versions before extractors into extractors.

    Runs once, when `extractors.json` does not exist. Old keys are not read
    here: each migrated extractor names its old credential item, and the key
    vault moves that key the first time it is opened. The extractors file is
    written before old settings are removed, so an interrupted migration is
    never repeated and never loses anything.

    An extractor is created for each provider with a saved-key flag, or with
    settings that differ from the old defaults. Every saved-key flag yields
    an extractor, even one whose old settings are missing or unusable, so no
    old key is left behind: such an extractor waits for its model or base
    URL to be entered before it can run.
    """
    settings_file = app_settings_path()
    # An old file that can't be read is migrated as empty but left in place,
    # so nothing it holds is lost.
    old_or_none = _read_json_object(settings_file)
    flags_or_none = _read_json_object(legacy_preferences_path())
    old = old_or_none or {}
    flags = flags_or_none or {}
    common = {
        name: old.get(name)
        for name in ("request_timeout_sec", "parallel_requests", "request_delay_sec")
        if old.get(name) is not None
    }
    candidates: list[dict[str, object]] = []
    gemini_model = str(old.get("default_gemini_model") or "").strip()
    gemini_tier = str(old.get("service_tier") or "").strip().lower()
    if (
        flags.get(LEGACY_SAVED_KEY_FLAGS["gemini"]) is True
        or gemini_model not in ("", LEGACY_DEFAULT_GEMINI_MODEL)
        or gemini_tier not in ("", LEGACY_DEFAULT_SERVICE_TIER)
    ):
        candidates.append({
            "provider": "gemini",
            "model": old.get("default_gemini_model") or LEGACY_DEFAULT_GEMINI_MODEL,
            "service_tier": old.get("service_tier"),
        })
    if flags.get(LEGACY_SAVED_KEY_FLAGS["openrouter"]) is True or old.get("default_openrouter_model"):
        candidates.append({
            "provider": "openrouter",
            "model": old.get("default_openrouter_model") or "",
            "reasoning_effort": old.get("reasoning_effort"),
        })
    if flags.get(LEGACY_SAVED_KEY_FLAGS["openai_compatible"]) is True or old.get("openai_base_url"):
        candidates.append({
            "provider": "openai_compatible",
            "model": "",
            "base_url": old.get("openai_base_url"),
            "response_format": old.get("openai_response_format"),
        })

    migrated = []
    for candidate in candidates:
        provider = str(candidate["provider"])
        has_key = flags.get(LEGACY_SAVED_KEY_FLAGS[provider]) is True
        values = {
            **common,
            **{name: value for name, value in candidate.items() if value is not None},
            "name": provider_label(provider).replace(" (Experimental)", ""),
            "key_saved": has_key,
            "legacy_credential": LEGACY_CREDENTIAL_ITEMS[provider] if has_key else "",
        }
        try:
            migrated.append(_build(values, extractor_id=str(uuid.uuid4()), require_model=False))
        except ExtractorError:
            if not has_key:
                continue
            # Keep the key with an extractor that asks for its settings again.
            bare = {name: values[name] for name in ("provider", "name", "key_saved", "legacy_credential")}
            migrated.append(_build(bare, extractor_id=str(uuid.uuid4()), require_model=False))
    save_extractors(migrated)
    if settings_file.is_file() and old_or_none is not None:
        save_app_settings(load_app_settings())
    if flags_or_none is not None:
        legacy_preferences_path().unlink(missing_ok=True)
    return migrated


def legacy_claims() -> dict[str, str]:
    """Old credential items still waiting to move into the key vault, by extractor id."""
    return {item.id: item.legacy_credential for item in load_extractors() if item.legacy_credential}


@_serialized
def record_legacy_adoption(result: Mapping[str, bool]) -> None:
    """Record which migrated extractors received their old key."""
    extractors = load_extractors()
    for index, item in enumerate(extractors):
        if item.id in result:
            extractors[index] = replace(item, key_saved=bool(result[item.id]), legacy_credential="")
    save_extractors(extractors)


def run_settings_for(extractor: Extractor, app_settings: AppSettings) -> RunSettings:
    """The concrete settings a run or retry uses with this extractor."""
    if not extractor.model:
        raise ExtractorError(f"Choose a model for the “{extractor.name}” extractor in Settings before running.")
    if extractor.provider == "openai_compatible" and not extractor.base_url:
        raise ExtractorError(f"Enter the API base URL for the “{extractor.name}” extractor in Settings before running.")
    return RunSettings(
        primary_model=extractor.model,
        provider=extractor.provider,
        request_timeout_sec=extractor.request_timeout_sec,
        parallel_requests=extractor.parallel_requests,
        request_delay_sec=extractor.request_delay_sec,
        max_upload_size_mb=app_settings.max_upload_size_mb,
        pdf_parser=app_settings.pdf_parser,
        reasoning_effort=extractor.reasoning_effort,
        service_tier=extractor.service_tier,
        openrouter_provider=extractor.openrouter_provider,
        openrouter_allow_fallbacks=extractor.openrouter_allow_fallbacks,
        base_url=extractor.base_url,
        response_format=extractor.response_format,
        context_length=extractor.context_length,
        max_output_tokens=extractor.max_output_tokens,
        extractor_id=extractor.id,
        extractor=extractor.snapshot(),
    )


def check_model(extractor: Extractor, api_key: str | None) -> None:
    """Ask the provider whether the extractor's model can be used with this key.

    Network failures (RuntimeError) propagate; an unusable model raises
    ExtractorError. OpenAI-compatible servers have no catalog to check.
    """
    if extractor.provider == "gemini":
        available = {option.name for option in list_live_models(api_key or "", "gemini")}
        if extractor.model not in available:
            raise ExtractorError(
                f"Gemini model `{extractor.model}` isn't available for this key. Check the model ID."
            )
    elif extractor.provider == "openrouter":
        try:
            validate_model_option("openrouter", extractor.model, api_key or "")
        except ValueError as exc:
            raise ExtractorError(str(exc)) from None


@dataclass(frozen=True)
class ProjectExtractor:
    """The extractor a project will use next, if any."""

    extractor: Extractor | None
    # True when the project's extractor has since been deleted.
    deleted: bool = False


def project_extractor(project_dir: Path) -> ProjectExtractor:
    """Find the extractor chosen for a project.

    A project from before extractors has no choice saved; its last run's
    provider and model preselect a matching extractor when there is one.
    """
    extractors = load_extractors()
    by_id = {item.id: item for item in extractors}
    chosen = load_selected_extractor_id(project_dir)
    if chosen:
        return ProjectExtractor(by_id.get(chosen), deleted=chosen not in by_id)
    previous = load_run_settings(project_dir)
    if previous is None:
        return ProjectExtractor(None)
    if previous.extractor_id:
        return ProjectExtractor(by_id.get(previous.extractor_id), deleted=previous.extractor_id not in by_id)
    match = next(
        (item for item in extractors if item.provider == previous.provider and item.model == previous.primary_model),
        None,
    )
    if match is not None:
        save_selected_extractor_id(project_dir, match.id)
    return ProjectExtractor(match)


def _refuse_unchosen(current: ProjectExtractor) -> Extractor:
    raise RuntimeError(
        "The extractor used for this project was deleted. Choose another."
        if current.deleted else "Choose an extractor for this project before running."
    )


def prepare_run(
    project_dir: Path,
    chosen: Extractor | None,
    read_key: Callable[[Extractor], str | None],
    *,
    fallback: Callable[[ProjectExtractor], Extractor] = _refuse_unchosen,
) -> tuple[RunSettings, str | None]:
    """The settings and key for a run or retry.

    Uses `chosen` if given, else the project's extractor, else whatever
    `fallback` returns (by default it refuses); the result is saved as the
    project's extractor. `read_key` reads the extractor's key and may block.
    A run that needs a key and has none is refused with RuntimeError.
    """
    extractor = chosen
    if extractor is None:
        current = project_extractor(project_dir)
        extractor = current.extractor or fallback(current)
    save_selected_extractor_id(project_dir, extractor.id)
    settings = run_settings_for(extractor, load_app_settings())
    key = read_key(extractor)
    if extractor.requires_key and not key:
        raise RuntimeError(f"Add an API key for the “{extractor.name}” extractor in Settings before running.")
    return settings, key
