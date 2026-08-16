"""Versioned, model-fingerprinted class-ID canonicalization.

The model checkpoint remains immutable.  A mapping is applied only when a
complete, validated permutation exists for the exact SHA-256 of the source
``.pt`` file.  Raw model output is always preserved for auditability.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


CANONICAL_BRIDGE_NAMES: dict[int, str] = {
    0: "Crack",
    1: "Efflorescence_Leaching",
    2: "Exposed Rebar",
    3: "Spalling",
    4: "Staining_Infiltration",
    5: "Corrosion",
    6: "Biological_Growth",
    7: "Pothole Asphalt",
    8: "Expansion Joint",
    9: "Guardrail Damaged",
    10: "Control Point",
}


class ClassMappingError(RuntimeError):
    """Raised when an enabled mapping is incomplete, ambiguous, or stale."""


def file_sha256(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


@dataclass(frozen=True)
class CanonicalClass:
    raw_class_id: int
    raw_class_name: str
    class_id: int
    class_name: str
    mapping_applied: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_class_id": self.raw_class_id,
            "raw_class_name": self.raw_class_name,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "class_mapping_applied": self.mapping_applied,
        }


class ModelClassRemapper:
    """Validated mapping for one model type and one exact source checkpoint."""

    def __init__(
        self,
        *,
        model_type: str,
        model_path: str | os.PathLike[str],
        raw_names: Mapping[int | str, str],
        config_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.model_type = str(model_type)
        self.model_path = str(model_path)
        self.raw_names = {int(key): str(value) for key, value in raw_names.items()}
        self.model_sha256 = file_sha256(self.model_path)
        configured_path = config_path or os.environ.get("CLASS_MAPPING_CONFIG")
        self.config_path = str(
            Path(configured_path)
            if configured_path
            else Path(__file__).resolve().with_name("class_mapping_config.json")
        )
        self.enabled = False
        self.status = "disabled"
        self.mapping: dict[int, tuple[int, str]] = {}
        self._load()

    def _load(self) -> None:
        path = Path(self.config_path)
        if not path.exists():
            self.status = "config_missing"
            return

        with path.open("r", encoding="utf-8") as stream:
            root = json.load(stream)

        if root.get("schema_version") != 1:
            raise ClassMappingError("class mapping schema_version must be 1")

        model_cfg = (root.get("models") or {}).get(self.model_type)
        if not model_cfg:
            self.status = "model_config_missing"
            return

        self.enabled = bool(model_cfg.get("enabled", False))
        self.status = str(model_cfg.get("status", "disabled"))
        if not self.enabled:
            return

        if self.status != "approved":
            raise ClassMappingError(
                f"{self.model_type} mapping is enabled but status={self.status!r}; "
                "status must be 'approved'"
            )

        expected_hash = str(model_cfg.get("model_sha256", "")).upper()
        if len(expected_hash) != 64 or expected_hash != self.model_sha256:
            raise ClassMappingError(
                f"{self.model_type} model SHA-256 mismatch: "
                f"config={expected_hash or '<missing>'}, actual={self.model_sha256}"
            )

        raw_name_cfg = {
            int(key): str(value)
            for key, value in (model_cfg.get("raw_names") or {}).items()
        }
        if raw_name_cfg != self.raw_names:
            raise ClassMappingError(
                f"{self.model_type} raw_names do not match checkpoint metadata: "
                f"config={raw_name_cfg}, checkpoint={self.raw_names}"
            )

        raw_mapping = model_cfg.get("class_permutation_map") or {}
        expected_ids = set(self.raw_names)
        mapping_ids = {int(key) for key in raw_mapping}
        if mapping_ids != expected_ids:
            raise ClassMappingError(
                f"{self.model_type} mapping must cover every raw ID exactly once: "
                f"expected={sorted(expected_ids)}, got={sorted(mapping_ids)}"
            )

        canonical_names = (
            CANONICAL_BRIDGE_NAMES if self.model_type == "bridge" else None
        )
        parsed: dict[int, tuple[int, str]] = {}
        canonical_ids: list[int] = []
        canonical_labels: list[str] = []
        for raw_id in sorted(expected_ids):
            item = raw_mapping[str(raw_id)]
            canonical_id = int(item["canonical_id"])
            canonical_name = str(item["canonical_name"])
            if canonical_names is not None:
                expected_name = canonical_names.get(canonical_id)
                if expected_name != canonical_name:
                    raise ClassMappingError(
                        f"invalid canonical pair for raw ID {raw_id}: "
                        f"{canonical_id} must be {expected_name!r}, got {canonical_name!r}"
                    )
            parsed[raw_id] = (canonical_id, canonical_name)
            canonical_ids.append(canonical_id)
            canonical_labels.append(canonical_name)

        if len(set(canonical_ids)) != len(canonical_ids):
            raise ClassMappingError("canonical IDs must form a one-to-one permutation")
        if len(set(canonical_labels)) != len(canonical_labels):
            raise ClassMappingError("canonical names must form a one-to-one permutation")

        self.mapping = parsed

    @property
    def mapping_applied(self) -> bool:
        return self.enabled and bool(self.mapping)

    def remap(self, raw_class_id: int, raw_class_name: str | None = None) -> CanonicalClass:
        raw_id = int(raw_class_id)
        raw_name = str(
            raw_class_name
            if raw_class_name is not None
            else self.raw_names.get(raw_id, f"class_{raw_id}")
        )
        if not self.mapping_applied:
            return CanonicalClass(raw_id, raw_name, raw_id, raw_name, False)
        if raw_id not in self.mapping:
            raise ClassMappingError(f"unmapped raw class ID at runtime: {raw_id}")
        canonical_id, canonical_name = self.mapping[raw_id]
        return CanonicalClass(raw_id, raw_name, canonical_id, canonical_name, True)

    def remap_detection(
        self, raw_class_id: int, raw_class_name: str | None = None
    ) -> dict[str, Any]:
        return self.remap(raw_class_id, raw_class_name).as_dict()

    def info(self) -> dict[str, Any]:
        return {
            "model_type": self.model_type,
            "model_path": self.model_path,
            "model_sha256": self.model_sha256,
            "config_path": self.config_path,
            "enabled": self.enabled,
            "status": self.status,
            "mapping_applied": self.mapping_applied,
            "mapped_raw_ids": sorted(self.mapping),
        }
