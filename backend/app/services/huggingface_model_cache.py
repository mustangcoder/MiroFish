"""Persistent Hugging Face model-cache preflight for OASIS simulations."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


class ModelCacheError(RuntimeError):
    pass


class HuggingFaceModelCache:
    WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")

    def __init__(
        self,
        *,
        cache_root=None,
        required_models=None,
        timeout_seconds=None,
        runner=subprocess.run,
    ):
        self.cache_root = Path(cache_root or os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
        configured = os.environ.get("HF_REQUIRED_MODELS", "")
        self.required_models = list(required_models if required_models is not None else filter(None, (item.strip() for item in configured.split(","))))
        self.timeout_seconds = int(timeout_seconds or os.environ.get("HF_MODEL_DOWNLOAD_TIMEOUT_SECONDS", "900"))
        self.runner = runner

    def _snapshot_dirs(self, model_id):
        repo_dir = "models--" + model_id.replace("/", "--")
        snapshots = self.cache_root / "hub" / repo_dir / "snapshots"
        return list(snapshots.iterdir()) if snapshots.exists() else []

    def is_ready(self, model_id):
        for snapshot in self._snapshot_dirs(model_id):
            if (snapshot / "config.json").exists() and any((snapshot / name).exists() for name in self.WEIGHT_FILES):
                return True
        return False

    def ensure_ready(self):
        missing = [model for model in self.required_models if not self.is_ready(model)]
        if not missing:
            return
        script = Path(__file__).resolve().parents[2] / "scripts" / "prefetch_huggingface.py"
        command = [sys.executable, str(script), *missing]
        environment = os.environ.copy()
        environment.setdefault("HF_HOME", str(self.cache_root))
        environment.setdefault("HF_HUB_DISABLE_XET", "1")
        environment.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
        try:
            self.runner(command, timeout=self.timeout_seconds, check=True, env=environment)
        except subprocess.TimeoutExpired as error:
            raise ModelCacheError(f"Hugging Face 推荐模型下载超时（{self.timeout_seconds}秒）") from error
        except subprocess.CalledProcessError as error:
            raise ModelCacheError("Hugging Face 推荐模型下载失败，请检查网络和缓存目录") from error
        still_missing = [model for model in missing if not self.is_ready(model)]
        if still_missing:
            raise ModelCacheError("Hugging Face 推荐模型缓存不完整: " + ", ".join(still_missing))
