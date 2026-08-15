from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from kestrel.nlq import model_install
from kestrel.nlq.semantic import default_catalog_path


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self) -> Iterator[bytes]:
        yield self.payload


class _Client:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.requested: list[str] = []

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def stream(self, method: str, url: str) -> _Response:
        assert method == "GET"
        self.requested.append(url)
        return _Response(self.payloads[url.rsplit("/", 1)[-1]])


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _manifest(tmp_path: Path, payloads: dict[str, bytes]) -> Path:
    manifest = tmp_path / "model.yml"
    manifest.write_text(
        f"""
version: "test"
model_id: example/test-model
revision: pinned-revision
license: Apache-2.0
base_url: https://models.example.invalid/resolve
common_files:
  - path: tokenizer.json
    size: {len(payloads['tokenizer.json'])}
    sha256: {_sha(payloads['tokenizer.json'])}
architectures:
  test:
    path: onnx/model.onnx
    size: {len(payloads['model.onnx'])}
    sha256: {_sha(payloads['model.onnx'])}
  fallback:
    path: onnx/model.onnx
    size: {len(payloads['model.onnx'])}
    sha256: {_sha(payloads['model.onnx'])}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return manifest


def test_installer_downloads_verifies_and_reuses_pinned_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads = {"tokenizer.json": b"tokenizer", "model.onnx": b"onnx-model"}
    manifest = _manifest(tmp_path, payloads)
    target = tmp_path / "installed"
    first_client = _Client(payloads)
    monkeypatch.setattr(model_install.httpx, "Client", lambda **_: first_client)

    metadata = model_install.install_model(
        target=target,
        manifest=manifest,
        architecture="test",
    )

    assert metadata["revision"] == "pinned-revision"
    assert (target / "tokenizer.json").read_bytes() == b"tokenizer"
    assert (target / "onnx" / "model.onnx").read_bytes() == b"onnx-model"
    assert len(first_client.requested) == 2
    assert model_install.installed_model_metadata(
        target, manifest=manifest
    ) == metadata

    marker = target / "kestrel-model.json"
    tampered_metadata = json.loads(marker.read_text(encoding="utf-8"))
    tampered_metadata["model_id"] = "unverified/replacement"
    marker.write_text(json.dumps(tampered_metadata), encoding="utf-8")
    assert model_install.installed_model_metadata(target, manifest=manifest) is None

    second_client = _Client(payloads)
    monkeypatch.setattr(model_install.httpx, "Client", lambda **_: second_client)
    model_install.install_model(
        target=target,
        manifest=manifest,
        architecture="test",
    )
    assert second_client.requested == []


def test_default_model_resources_follow_configured_project_root_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "installed-app"
    config = root / "config"
    config.mkdir(parents=True)
    model_path = root / "private-runtime" / "models" / "mini"
    monkeypatch.setenv("KESTREL_PROJECT_ROOT", str(root))
    monkeypatch.setenv("KESTREL_NLQ_MODEL_PATH", str(model_path))

    assert model_install.default_manifest_path() == config / "nlq_model.yml"
    assert default_catalog_path() == config / "nlq_intents.yml"
    assert model_install.default_model_path() == model_path


def test_installer_rejects_bad_checksum_and_removes_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = {"tokenizer.json": b"expected", "model.onnx": b"model"}
    manifest = _manifest(tmp_path, expected)
    bad_payloads = {"tokenizer.json": b"tampered", "model.onnx": b"model"}
    monkeypatch.setattr(
        model_install.httpx,
        "Client",
        lambda **_: _Client(bad_payloads),
    )
    target = tmp_path / "installed"

    with pytest.raises(RuntimeError, match="Checksum or size validation failed"):
        model_install.install_model(
            target=target,
            manifest=manifest,
            architecture="test",
        )

    assert not (target / "tokenizer.json").exists()
    assert not (target / "tokenizer.json.part").exists()
    assert not (target / "kestrel-model.json").exists()
