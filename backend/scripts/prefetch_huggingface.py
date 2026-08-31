"""Download the minimal runtime files for OASIS Hugging Face models."""

import os
from pathlib import Path
import sys


PATTERNS = [
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.*",
    "merges.txt",
    "*.model",
    "model.safetensors",
]


def main(model_ids):
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
    from huggingface_hub import snapshot_download

    cache_root = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    cache_dir = cache_root / "hub"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for model_id in model_ids:
        print(f"[hf-prefetch] downloading {model_id} to {cache_dir}", flush=True)
        snapshot_download(
            repo_id=model_id,
            cache_dir=cache_dir,
            allow_patterns=PATTERNS,
            max_workers=4,
        )
        print(f"[hf-prefetch] ready {model_id}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:] or ["Twitter/twhin-bert-base"])
