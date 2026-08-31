import subprocess

import pytest

from app.services.huggingface_model_cache import HuggingFaceModelCache, ModelCacheError


def test_ready_cache_skips_download(tmp_path):
    snapshot = tmp_path / "hub" / "models--Twitter--twhin-bert-base" / "snapshots" / "revision"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}")
    (snapshot / "pytorch_model.bin").write_bytes(b"weights")
    calls = []

    cache = HuggingFaceModelCache(
        cache_root=tmp_path,
        required_models=["Twitter/twhin-bert-base"],
        runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    cache.ensure_ready()

    assert calls == []


def test_missing_cache_downloads_with_timeout(tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        snapshot = tmp_path / "hub" / "models--Twitter--twhin-bert-base" / "snapshots" / "revision"
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text("{}")
        (snapshot / "model.safetensors").write_bytes(b"weights")

    cache = HuggingFaceModelCache(
        cache_root=tmp_path,
        required_models=["Twitter/twhin-bert-base"],
        timeout_seconds=42,
        runner=runner,
    )

    cache.ensure_ready()

    assert calls[0][1]["timeout"] == 42
    assert "Twitter/twhin-bert-base" in calls[0][0]


def test_download_timeout_becomes_readable_error(tmp_path):
    def runner(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("prefetch", 3)

    cache = HuggingFaceModelCache(
        cache_root=tmp_path,
        required_models=["Twitter/twhin-bert-base"],
        timeout_seconds=3,
        runner=runner,
    )

    with pytest.raises(ModelCacheError, match="下载超时"):
        cache.ensure_ready()
