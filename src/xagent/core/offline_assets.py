"""Prerequisite checks for runtime assets prepared during online builds."""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from types import FunctionType
from typing import Any
from urllib.parse import urlsplit

from ..config import (
    get_deepdoc_model_home,
    get_sandbox_pip_index_url,
    get_tiktoken_cache_candidates,
)


class OfflineAssetError(RuntimeError):
    """A missing packaged asset will not be downloaded at runtime."""


# tiktoken 0.12.0's public encoding URLs and expected content hashes. A cache
# file must pass the hash check *before* get_encoding: tiktoken deletes a bad
# cache entry and fetches its replacement otherwise.
_ENCODING_BLOBS = {
    "r50k_base": (
        "r50k_base",
        "306cd27f03c1a714eca7108e03d66b7dc042abe8c258b44c199a7ed9838dd930",  # pragma: allowlist secret - public asset SHA256
    ),
    "p50k_base": (
        "p50k_base",
        "94b5ca7dff4d00767bc256fdd1b27e5b17361d7b8a5f968547f9f23eb70d2069",  # pragma: allowlist secret - public asset SHA256
    ),
    "p50k_edit": (
        "p50k_base",
        "94b5ca7dff4d00767bc256fdd1b27e5b17361d7b8a5f968547f9f23eb70d2069",  # pragma: allowlist secret - public asset SHA256
    ),
    "cl100k_base": (
        "cl100k_base",
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",  # pragma: allowlist secret - public asset SHA256
    ),
    "o200k_base": (
        "o200k_base",
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",  # pragma: allowlist secret - public asset SHA256
    ),
    "o200k_harmony": (
        "o200k_base",
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",  # pragma: allowlist secret - public asset SHA256
    ),
    "gpt2": ("gpt2", ""),
}
_CL100K_URL = (
    "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
)
CL100K_BASE_CACHE_KEY = hashlib.sha1(_CL100K_URL.encode()).hexdigest()


def _encoding_cache_keys(encoding: str) -> list[tuple[str, str]]:
    if encoding == "gpt2":
        return [
            (
                "https://openaipublic.blob.core.windows.net/gpt-2/encodings/main/vocab.bpe",
                "1ce1664773c50f3e0cc8842619a93edc4624525b728b188a9e0be33b7726adc5",  # pragma: allowlist secret - public asset SHA256
            ),
            (
                "https://openaipublic.blob.core.windows.net/gpt-2/encodings/main/encoder.json",
                "196139668be63f3b5d6574427317ae82f612a97c5d1cdaf36ed2256dbf636783",  # pragma: allowlist secret - public asset SHA256
            ),
        ]
    name, digest = _ENCODING_BLOBS[encoding]
    return [
        (
            f"https://openaipublic.blob.core.windows.net/encodings/{name}.tiktoken",
            digest,
        )
    ]


@lru_cache(maxsize=1)
def _require_tiktoken_version() -> None:
    installed = version("tiktoken")
    if installed != "0.12.0":
        raise OfflineAssetError(
            "The offline tiktoken cache manifest covers tiktoken 0.12.0, "
            f"not {installed}. Install the locked version and prewarm its "
            "encoding assets before deployment."
        )


def require_tiktoken_encoding(encoding_name: str = "cl100k_base") -> Any:
    """Load an encoding only after every source blob exists with the right hash."""
    if encoding_name not in _ENCODING_BLOBS:
        raise OfflineAssetError(
            f"tiktoken encoding {encoding_name!r} has no verified offline cache "
            "manifest. Choose a supported encoding or package it explicitly."
        )
    entries = _encoding_cache_keys(encoding_name)
    for directory in get_tiktoken_cache_candidates():
        files = tuple(
            directory / hashlib.sha1(url.encode()).hexdigest() for url, _ in entries
        )
        try:
            stats = tuple(
                (file.stat().st_size, file.stat().st_mtime_ns) for file in files
            )
        except OSError:
            continue
        if _encoding_blobs_valid(files, tuple(digest for _, digest in entries), stats):
            _require_tiktoken_version()
            try:
                encoding = _local_tiktoken_encoding(encoding_name, directory)
            except (OSError, ValueError) as exc:
                raise OfflineAssetError(
                    f"tiktoken encoding {encoding_name!r} changed after its "
                    "local cache was verified. Restore the prepared cache; "
                    "no public fetch was attempted."
                ) from exc
            from tiktoken import registry

            with registry._lock:
                registry.ENCODINGS[encoding_name] = encoding
            return encoding
    raise OfflineAssetError(
        f"tiktoken encoding {encoding_name!r} is missing or corrupt in "
        "TIKTOKEN_CACHE_DIR / DEEPDOC_TIKTOKEN_CACHE_DIR. Preload it during "
        "an online build (deepdoc-download-models caches cl100k_base; other "
        "encodings can be warmed with tiktoken.get_encoding) and copy that "
        "cache to the deployed host before starting. No public fetch attempted."
    )


def prepare_runtime_tokenizers() -> None:
    """Register every pinned encoding before third-party consumers run.

    DeepDoc and LangChain call tiktoken directly. The registry is populated
    from our local-only loader under tiktoken's own lock so those consumers
    reuse verified encodings and cannot trigger a first-use public fetch.
    Missing blobs fail startup with a concrete offline prerequisite error.
    """
    require_tiktoken_encoding("cl100k_base")
    for name in _ENCODING_BLOBS:
        if name != "cl100k_base":
            require_tiktoken_encoding(name)


@lru_cache(maxsize=32)
def _encoding_blobs_valid(
    files: tuple[Path, ...],
    digests: tuple[str, ...],
    _stats: tuple[tuple[int, int], ...],
) -> bool:
    try:
        return all(
            hashlib.sha256(file.read_bytes()).hexdigest() == digest
            for file, digest in zip(files, digests)
        )
    except OSError:
        return False


@lru_cache(maxsize=8)
def _local_tiktoken_encoding(encoding_name: str, directory: Path) -> Any:
    """Call pinned native constructors and parsers with verified local bytes.

    Private function globals leave tiktoken's network-capable loaders untouched.
    Neither the native parsers nor the constructor may create another cache.
    """
    from tiktoken import registry
    from tiktoken.core import Encoding
    from tiktoken.load import data_gym_to_mergeable_bpe_ranks, load_tiktoken_bpe

    paths = {
        url: (directory / hashlib.sha1(url.encode()).hexdigest(), digest)
        for url, digest in _encoding_cache_keys(encoding_name)
    }

    def local_bytes(url: str, expected_hash: str | None = None) -> bytes:
        if url not in paths:
            raise OfflineAssetError(
                f"Unexpected tiktoken source {url!r}; local-only loader refused it."
            )
        path, digest = paths[url]
        if expected_hash is not None and expected_hash != digest:
            raise OfflineAssetError(
                f"Unexpected tiktoken hash for {url!r}; local-only loader refused it."
            )
        contents = path.read_bytes()
        if hashlib.sha256(contents).hexdigest() != digest:
            raise ValueError(f"tiktoken cache blob {path} changed after verification")
        return contents

    def private_parser(parser: object) -> FunctionType:
        if not isinstance(parser, FunctionType):
            raise OfflineAssetError("Unsupported tiktoken parser implementation")
        private = FunctionType(
            parser.__code__,
            {**parser.__globals__, "read_file_cached": local_bytes},
            parser.__name__,
            parser.__defaults__,
            parser.__closure__,
        )
        private.__kwdefaults__ = parser.__kwdefaults__
        return private

    local_bpe = private_parser(load_tiktoken_bpe)
    local_gpt2 = private_parser(data_gym_to_mergeable_bpe_ranks)

    registry._find_constructors()
    constructors = registry.ENCODING_CONSTRUCTORS
    if constructors is None:
        raise OfflineAssetError("Pinned tiktoken constructors are unavailable")
    constructor = constructors[encoding_name]
    base_constructor = constructors["o200k_base"]
    if not isinstance(constructor, FunctionType) or not isinstance(
        base_constructor, FunctionType
    ):
        raise OfflineAssetError("Unsupported tiktoken constructor implementation")
    private_globals = {
        **constructor.__globals__,
        "load_tiktoken_bpe": local_bpe,
        "data_gym_to_mergeable_bpe_ranks": local_gpt2,
    }
    # o200k_harmony calls o200k_base by global name: it must use the same
    # private globals, not the original plugin function's public loader.
    private_globals["o200k_base"] = FunctionType(
        base_constructor.__code__, private_globals
    )
    return Encoding(**FunctionType(constructor.__code__, private_globals)())


def require_deepdoc_local_assets() -> None:
    """Force DeepDoc's own local model and NLTK resolvers to fail closed."""
    os.environ["DEEPDOC_OFFLINE"] = "1"
    os.environ["DEEPDOC_MODEL_PROVIDER"] = "local"
    model_home = get_deepdoc_model_home()
    os.environ["DEEPDOC_MODEL_HOME"] = str(model_home)
    try:
        from deepdoc.config import PdfModelConfig
        from deepdoc.depend.nltk_manager import ensure_nltk_data

        config = PdfModelConfig.from_env()
        config.resolve_vision_model_dir()
        config.resolve_xgb_model_dir()
        ensure_nltk_data(offline=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise OfflineAssetError(
            f"DeepDoc local PDF assets missing under {model_home}: {exc}. "
            "Run deepdoc-download-models on a connected build host, copy the "
            "model/NLTK cache to DEEPDOC_MODEL_HOME, and set "
            "DEEPDOC_OFFLINE=1. No ModelScope/NLTK download attempted."
        ) from exc


def sandbox_image_missing_error(image: str) -> OfflineAssetError:
    return OfflineAssetError(
        f"Sandbox image {image!r} is not present on this Docker host. "
        "Preload it with docker pull on a connected host followed by docker "
        "save/load, or set SANDBOX_IMAGE to a locally available tag. "
        "Runtime will not pull from a registry."
    )


def sandbox_pip_disabled_error(packages: list[str]) -> OfflineAssetError:
    return OfflineAssetError(
        f"Sandbox Python requirements {', '.join(packages)} are not available "
        "in the image or a local wheelhouse. Bake them into "
        "docker/Dockerfile.sandbox during an online build, or explicitly "
        "configure XAGENT_SANDBOX_PIP_INDEX_URL to a trusted LAN package mirror."
    )


def configured_lan_pip_index() -> str | None:
    """Allow runtime pip only with an explicit, non-public operator override."""
    value = get_sandbox_pip_index_url()
    if value is None:
        return None
    host = urlsplit(value).hostname
    if host is None:
        raise OfflineAssetError("XAGENT_SANDBOX_PIP_INDEX_URL must be an HTTP(S) URL")
    host = host.lower()
    if host in (
        "pypi.org",
        "pypi.python.org",
        "files.pythonhosted.org",
    ) or host.endswith((".pypi.org", ".pythonhosted.org")):
        raise OfflineAssetError("XAGENT_SANDBOX_PIP_INDEX_URL must not use public PyPI")
    return value
