"""Runtime assets are consumed locally rather than downloaded on cache miss."""

import builtins
import hashlib
import io
import os
from pathlib import Path

import pytest
import tiktoken
from tiktoken import load as native_load
from tiktoken import registry
from tiktoken_ext import openai_public

from xagent.core.offline_assets import (
    OfflineAssetError,
    _encoding_blobs_valid,
    _encoding_cache_keys,
    _local_tiktoken_encoding,
    prepare_runtime_tokenizers,
    require_tiktoken_encoding,
)
from xagent.core.tools.core.RAG_tools.utils.token_utils import (
    num_tokens_from_string,
    split_text_by_tokens,
)
from xagent.providers.pdf_parser.deepdoc import DeepDocParser


@pytest.fixture
def copied_tokenizer_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy an explicitly prepared full cache to a different absolute path."""
    source = os.environ.get("TIKTOKEN_CACHE_DIR")
    if not source:
        pytest.fail("Set TIKTOKEN_CACHE_DIR to a complete prepared seven-alias cache")
    cache = tmp_path / "copied-cache"
    cache.mkdir()
    urls = {
        url
        for encoding in (
            "gpt2",
            "r50k_base",
            "p50k_base",
            "p50k_edit",
            "cl100k_base",
            "o200k_base",
            "o200k_harmony",
        )
        for url, _ in _encoding_cache_keys(encoding)
    }
    for url in urls:
        key = hashlib.sha1(url.encode()).hexdigest()
        blob = Path(source) / key
        if not blob.is_file():
            pytest.fail(f"Prepared TIKTOKEN_CACHE_DIR lacks required blob {key}")
        (cache / key).write_bytes(blob.read_bytes())
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(cache))
    return cache


@pytest.fixture
def fresh_tokenizer_caches():
    _encoding_blobs_valid.cache_clear()
    _local_tiktoken_encoding.cache_clear()
    try:
        yield
    finally:
        _encoding_blobs_valid.cache_clear()
        _local_tiktoken_encoding.cache_clear()


@pytest.fixture
def fresh_native_encodings(fresh_tokenizer_caches: None):
    with registry._lock:
        previous = dict(registry.ENCODINGS)
        registry.ENCODINGS.clear()
    try:
        yield
    finally:
        with registry._lock:
            registry.ENCODINGS.clear()
            registry.ENCODINGS.update(previous)


def _refuse_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: pytest.fail("public tiktoken download attempted"),
    )


def test_native_tokenizers_use_copied_read_only_cache_without_writes(
    copied_tokenizer_cache: Path,
    fresh_native_encodings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refuse_http(monkeypatch)
    cache = copied_tokenizer_cache
    original_open = builtins.open
    original_io_open = io.open
    loaders = (
        native_load.read_file_cached,
        native_load.load_tiktoken_bpe,
        native_load.data_gym_to_mergeable_bpe_ranks,
        openai_public.load_tiktoken_bpe,
        openai_public.data_gym_to_mergeable_bpe_ranks,
    )

    def guard_cache_write(opener):
        def guarded(file, mode="r", *args, **kwargs):
            if isinstance(file, (str, os.PathLike)):
                path = Path(file)
                if path.parent == cache and any(flag in mode for flag in "wax+"):
                    pytest.fail(
                        f"Attempted to write to read-only tokenizer cache: {path}"
                    )
            return opener(file, mode, *args, **kwargs)

        return guarded

    monkeypatch.setattr(builtins, "open", guard_cache_write(original_open))
    monkeypatch.setattr(io, "open", guard_cache_write(original_io_open))
    before = {file.name for file in cache.iterdir()}
    for file in cache.iterdir():
        file.chmod(0o444)
    cache.chmod(0o555)
    try:
        prepare_runtime_tokenizers()
        expected = {
            "gpt2": [31373, 995],
            "r50k_base": [31373, 995],
            "p50k_base": [31373, 995],
            "p50k_edit": [31373, 995],
            "cl100k_base": [15339, 1917],
            "o200k_base": [24912, 2375],
            "o200k_harmony": [24912, 2375],
        }
        for name, tokens in expected.items():
            encoding = tiktoken.get_encoding(name)
            assert encoding.encode("hello world") == tokens
            assert encoding.decode(tokens) == "hello world"
        assert (
            tiktoken.get_encoding("p50k_edit").encode_single_token("<|fim_prefix|>")
            == 50281
        )
        assert (
            tiktoken.get_encoding("cl100k_base").encode_single_token("<|fim_prefix|>")
            == 100258
        )
        assert (
            tiktoken.get_encoding("o200k_harmony").encode_single_token("<|start|>")
            == 200006
        )
        assert {file.name for file in cache.iterdir()} == before
        assert loaders == (
            native_load.read_file_cached,
            native_load.load_tiktoken_bpe,
            native_load.data_gym_to_mergeable_bpe_ranks,
            openai_public.load_tiktoken_bpe,
            openai_public.data_gym_to_mergeable_bpe_ranks,
        )
    finally:
        cache.chmod(0o755)
        for file in cache.iterdir():
            file.chmod(0o644)


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
@pytest.mark.parametrize("encoding", ["cl100k_base", "gpt2"])
def test_preflight_rejects_missing_or_corrupt_copied_blob(
    copied_tokenizer_cache: Path,
    fresh_tokenizer_caches: None,
    encoding: str,
    damage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refuse_http(monkeypatch)
    url, _ = _encoding_cache_keys(encoding)[-1]
    blob = copied_tokenizer_cache / hashlib.sha1(url.encode()).hexdigest()
    if damage == "missing":
        blob.unlink()
    else:
        blob.write_bytes(b"corrupt")
    with pytest.raises(OfflineAssetError, match="missing or corrupt"):
        require_tiktoken_encoding(encoding)


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
@pytest.mark.parametrize(
    ("encoding", "blob_index"),
    [("cl100k_base", 0), ("gpt2", 0), ("gpt2", 1)],
)
def test_tokenizer_rejects_blob_changed_between_preflight_and_native_parse(
    copied_tokenizer_cache: Path,
    fresh_tokenizer_caches: None,
    encoding: str,
    blob_index: int,
    damage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refuse_http(monkeypatch)
    url, _ = _encoding_cache_keys(encoding)[blob_index]
    blob = copied_tokenizer_cache / hashlib.sha1(url.encode()).hexdigest()
    before = {file.name for file in copied_tokenizer_cache.iterdir()}
    original_read = Path.read_bytes
    first_read = True

    def race_blob_read(path: Path) -> bytes:
        nonlocal first_read
        contents = original_read(path)
        if path == blob and first_read:
            first_read = False
            if damage == "missing":
                blob.unlink()
            else:
                blob.write_bytes(b"corrupt")
        return contents

    monkeypatch.setattr(Path, "read_bytes", race_blob_read)
    with pytest.raises(
        OfflineAssetError, match="changed after its local cache was verified"
    ):
        require_tiktoken_encoding(encoding)
    assert not first_read
    assert {file.name for file in copied_tokenizer_cache.iterdir()} <= before


@pytest.mark.parametrize("cache_contents", [None, b"corrupt merge table"])
def test_token_count_refuses_missing_or_corrupt_encoder_without_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_contents: bytes | None
) -> None:
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    if cache_contents is not None:
        from xagent.core.offline_assets import CL100K_BASE_CACHE_KEY

        (tmp_path / CL100K_BASE_CACHE_KEY).write_bytes(cache_contents)
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: pytest.fail("public tiktoken download attempted"),
    )

    with pytest.raises(OfflineAssetError, match="Preload it during an online build"):
        num_tokens_from_string("An accurate token count is required")
    with pytest.raises(OfflineAssetError, match="Preload it during an online build"):
        split_text_by_tokens("An accurate token split is required", max_tokens=3)


@pytest.mark.parametrize("encoding", ["cl100k_base", "o200k_harmony", "gpt2"])
def test_tiktoken_local_constructor_cannot_fetch_after_cache_disappears(
    encoding: str,
    tmp_path: Path,
    fresh_tokenizer_caches: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Models the interval between the public preflight and plugin construction:
    # the directory still exists, but the blob has been removed.
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: pytest.fail("public tiktoken download attempted"),
    )
    with pytest.raises(FileNotFoundError):
        _local_tiktoken_encoding(encoding, tmp_path)


@pytest.mark.asyncio
async def test_local_deepdoc_pdf_refuses_missing_models_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPDOC_MODEL_HOME", str(tmp_path / "models"))
    monkeypatch.delenv("XAGENT_DEEPDOC_XINFERENCE_URL", raising=False)
    parser = DeepDocParser()

    with pytest.raises(OfflineAssetError, match="deepdoc-download-models"):
        await parser.parse("tests/resources/test_files/test.pdf")
