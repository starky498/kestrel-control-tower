"""Verified installer for the optional local Ask Kestrel embedding model."""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

import httpx
import yaml

from kestrel.config import Settings, project_config_path

_INSTALL_LOCK = Lock()


@dataclass(frozen=True)
class ModelFile:
    """One checksum-pinned file in the local model distribution."""

    path: str
    size: int
    sha256: str


def default_manifest_path() -> Path:
    """Return the project location of the pinned model manifest."""

    return project_config_path("nlq_model.yml")


def default_model_path() -> Path:
    """Return the model path selected by normal application configuration."""

    return Settings.load().nlq_model_path


def _load_manifest(
    path: Path, architecture: str
) -> tuple[dict[str, str], tuple[ModelFile, ...]]:
    payload: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    architectures = payload["architectures"]
    selected = architectures.get(architecture, architectures["fallback"])
    raw_files = [*payload["common_files"], selected]
    files = tuple(
        ModelFile(
            path=str(item["path"]),
            size=int(item["size"]),
            sha256=str(item["sha256"]),
        )
        for item in raw_files
    )
    metadata = {
        "model_id": str(payload["model_id"]),
        "revision": str(payload["revision"]),
        "license": str(payload["license"]),
        "base_url": str(payload["base_url"]).rstrip("/"),
        "onnx_file": str(selected["path"]),
        "architecture": architecture,
    }
    return metadata, files


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _valid(path: Path, expected: ModelFile) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == expected.size
        and _digest(path) == expected.sha256
    )


def _download(
    client: httpx.Client,
    url: str,
    target: Path,
    expected: ModelFile,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    hasher = hashlib.sha256()
    size = 0
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
                    hasher.update(chunk)
                    size += len(chunk)
        if size != expected.size or hasher.hexdigest() != expected.sha256:
            raise RuntimeError(f"Checksum or size validation failed for {expected.path}")
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def install_model(
    *,
    target: Path,
    manifest: Path | None = None,
    architecture: str | None = None,
) -> dict[str, object]:
    """Install the pinned public model without credentials.

    Existing files are reused only after their size and SHA-256 digest are verified.
    Downloads are written to a temporary sibling and atomically promoted after verification.
    """

    with _INSTALL_LOCK:
        source = (manifest or default_manifest_path()).resolve()
        resolved_architecture = (architecture or platform.machine()).casefold()
        metadata, files = _load_manifest(source, resolved_architecture)
        revision = metadata["revision"]
        with httpx.Client(timeout=httpx.Timeout(120.0), follow_redirects=True) as client:
            for item in files:
                destination = target / item.path
                if _valid(destination, item):
                    continue
                url = f"{metadata['base_url']}/{revision}/{item.path}"
                _download(client, url, destination, item)

        installed: dict[str, object] = {
            **metadata,
            "model_path": str(target.resolve()),
            "files": [
                {"path": item.path, "size": item.size, "sha256": item.sha256}
                for item in files
            ],
        }
        marker = target / "kestrel-model.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(installed, indent=2) + "\n", encoding="utf-8")
        return installed


def installed_model_metadata(
    target: Path,
    *,
    manifest: Path | None = None,
) -> dict[str, object] | None:
    """Return verified installation metadata, or ``None`` for an incomplete model."""

    marker = target / "kestrel-model.json"
    if not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        architecture = str(payload.get("architecture", platform.machine())).casefold()
        expected_metadata, files = _load_manifest(
            (manifest or default_manifest_path()).resolve(), architecture
        )
        verified_metadata_fields = (
            "model_id",
            "revision",
            "license",
            "base_url",
            "onnx_file",
            "architecture",
        )
        if any(
            payload.get(field) != expected_metadata[field]
            for field in verified_metadata_fields
        ):
            return None
        if not all(_valid(target / item.path, item) for item in files):
            return None
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, yaml.YAMLError):
        return None
    return payload


__all__ = [
    "ModelFile",
    "default_manifest_path",
    "default_model_path",
    "install_model",
    "installed_model_metadata",
]
