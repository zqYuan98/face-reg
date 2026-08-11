"""Versioned compatibility contract for auditing exported legacy vectors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from face_algorithm.runtime.model_registry import load_manifest
from face_algorithm.schemas import FEATURE_DIMENSION, FeatureVersion

__all__ = [
    "DEFAULT_COMPATIBILITY_MANIFEST",
    "LegacyCompatibility",
    "load_legacy_compatibility",
]

DEFAULT_COMPATIBILITY_MANIFEST = (
    Path(__file__).resolve().parents[2] / "legacy-compatibility.manifest.json"
)


class _StrictCompatibilitySchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


class CompatibleRecognizer(_StrictCompatibilitySchema):
    name: Literal["face_recognizer"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LegacyCompatibility(_StrictCompatibilitySchema):
    manifest_version: Literal["2026-08-04-v1"]
    feature_version: FeatureVersion
    feature_dimension: Literal[1024]
    recognizer: CompatibleRecognizer
    accepted_provenance: tuple[str, ...] = Field(min_length=1)
    models_manifest: Path

    @field_validator("feature_version", mode="before")
    @classmethod
    def convert_feature_version(cls, value: Any) -> Any:
        return FeatureVersion(value) if isinstance(value, str) else value

    @field_validator("accepted_provenance", mode="before")
    @classmethod
    def convert_provenance(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("models_manifest", mode="before")
    @classmethod
    def convert_models_manifest(cls, value: Any) -> Any:
        return Path(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.feature_version is not FeatureVersion.RECOGNIZER_V1_1024:
            raise ValueError("unsupported legacy feature version")
        if self.feature_dimension != FEATURE_DIMENSION:
            raise ValueError("unsupported legacy feature dimension")
        if any(not value.strip() for value in self.accepted_provenance):
            raise ValueError("accepted provenance values must be non-empty")
        if len(self.accepted_provenance) != len(set(self.accepted_provenance)):
            raise ValueError("accepted provenance values must be unique")
        return self


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def load_legacy_compatibility(
    path: str | Path = DEFAULT_COMPATIBILITY_MANIFEST,
) -> LegacyCompatibility:
    """Load the compatibility contract and cross-check the model manifest."""

    compatibility_path = Path(path).expanduser().resolve()
    payload = json.loads(
        compatibility_path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("legacy compatibility manifest root must be an object")
    normalized = dict(payload)
    configured_models_manifest = normalized.get("models_manifest")
    if isinstance(configured_models_manifest, str):
        models_manifest = Path(configured_models_manifest)
        if models_manifest.is_absolute():
            raise ValueError("models_manifest must be relative to compatibility manifest")
        resolved_models_manifest = (
            compatibility_path.parent / models_manifest
        ).resolve()
        if not resolved_models_manifest.is_relative_to(compatibility_path.parent):
            raise ValueError("models_manifest must stay within the manifest directory")
        normalized["models_manifest"] = resolved_models_manifest
    compatibility = LegacyCompatibility.model_validate(normalized)

    models = load_manifest(compatibility.models_manifest)
    recognizer = next(
        model for model in models.models if model.name == compatibility.recognizer.name
    )
    if recognizer.sha256 != compatibility.recognizer.sha256:
        raise ValueError("legacy compatibility recognizer SHA does not match models manifest")
    if recognizer.feature_version is not compatibility.feature_version:
        raise ValueError(
            "legacy compatibility feature version does not match models manifest"
        )
    if recognizer.expected.output_dimension != compatibility.feature_dimension:
        raise ValueError(
            "legacy compatibility feature dimension does not match models manifest"
        )
    return compatibility
