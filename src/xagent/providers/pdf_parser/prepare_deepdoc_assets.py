"""Materialize DeepDoc's downloaded bundles into a local-only runtime layout.

Run after ``deepdoc-download-models`` on the connected build host. ModelScope
may leave bundles nested under ``model_home/modelscope/...``; DeepDoc's local
provider only searches ``model_home/vision`` and ``model_home/xgb``.
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tokenizers-only",
        action="store_true",
        help="Warm only tiktoken encodings; do not resolve DeepDoc ONNX bundles.",
    )
    args = parser.parse_args(argv)

    if configured_home := os.getenv("DEEPDOC_MODEL_HOME"):
        home = Path(configured_home).expanduser()
    else:
        from ...config import get_deepdoc_model_home

        home = get_deepdoc_model_home()
    if not args.tokenizers_only:
        from deepdoc.common.model_store import resolve_bundle_dir, validate_bundle_dir

        for bundle in ("vision", "xgb"):
            source = Path(
                resolve_bundle_dir(bundle, model_home=str(home), provider="auto")
            ).resolve()
            target = (home / bundle).resolve()
            if source != target:
                shutil.copytree(source, target, dirs_exist_ok=True)
            complete, missing = validate_bundle_dir(bundle, target)
            if not complete:
                raise RuntimeError(
                    f"DeepDoc {bundle} bundle incomplete at {target}: "
                    f"{', '.join(missing)}"
                )
        import nltk  # type: ignore[import-untyped]
        from deepdoc.depend.nltk_manager import ensure_nltk_data

        nltk_dir = os.getenv("DEEPDOC_NLTK_DATA_DIR") or str(home / "nltk_data")
        original_search_path = nltk.data.path[:]
        try:
            # A transferable bundle must not silently borrow the build user's
            # globally installed corpora instead of populating this directory.
            nltk.data.path[:] = [str(Path(nltk_dir).expanduser().resolve())]
            ensure_nltk_data(data_dir=nltk_dir, offline=False)
        finally:
            nltk.data.path[:] = original_search_path
        from deepdoc.config import PdfModelConfig

        os.environ["DEEPDOC_MODEL_HOME"] = str(home)
        os.environ["DEEPDOC_MODEL_PROVIDER"] = "local"
        os.environ["DEEPDOC_OFFLINE"] = "1"
        config = PdfModelConfig.from_env()
        config.resolve_vision_model_dir()
        config.resolve_xgb_model_dir()

    # LangChain's chat/embedding counters may select any built-in tiktoken
    # family; warm every pinned source blob while this install/build has
    # network access, before the runtime local-only registrar runs.
    explicit_cache = os.getenv("TIKTOKEN_CACHE_DIR")
    if explicit_cache is not None:
        if not explicit_cache.strip():
            raise ValueError(
                "TIKTOKEN_CACHE_DIR is empty. Set it to a writable local "
                "cache directory before preparing offline encodings."
            )
        cache_dir = explicit_cache
    else:
        cache_dir = os.getenv("DEEPDOC_TIKTOKEN_CACHE_DIR") or str(
            home / "tiktoken_cache"
        )
    os.environ["TIKTOKEN_CACHE_DIR"] = cache_dir
    import tiktoken

    for encoding in (
        "cl100k_base",
        "o200k_base",
        "o200k_harmony",
        "r50k_base",
        "p50k_base",
        "p50k_edit",
        "gpt2",
    ):
        tiktoken.get_encoding(encoding)


if __name__ == "__main__":
    main()
