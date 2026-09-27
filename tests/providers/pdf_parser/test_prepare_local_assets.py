"""Connected preparation must produce a self-contained transferable asset tree."""

import os
from pathlib import Path

import nltk
import pytest
import tiktoken
from deepdoc.common import model_store
from deepdoc.depend import nltk_manager

from xagent.providers.pdf_parser.prepare_deepdoc_assets import main


@pytest.fixture
def prepared_sources(tmp_path, monkeypatch):
    home = tmp_path / "transfer"
    monkeypatch.setenv("DEEPDOC_MODEL_HOME", str(home))
    monkeypatch.setenv("DEEPDOC_MODEL_PROVIDER", "auto")
    monkeypatch.setenv("DEEPDOC_OFFLINE", "0")
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(home / "tokenizers"))
    monkeypatch.delenv("DEEPDOC_NLTK_DATA_DIR", raising=False)
    sources = {}
    for name, spec in model_store.BUNDLES.items():
        monkeypatch.delenv(spec.local_dir_env, raising=False)
        source = tmp_path / "download-cache" / name
        source.mkdir(parents=True)
        for filename in spec.required_files:
            (source / filename).write_bytes(f"prepared:{name}:{filename}".encode())
        sources[name] = source
    resolve = model_store.resolve_bundle_dir

    def resolve_prepared(name, **kwargs):
        if kwargs.get("provider") == "auto":
            return sources[name]
        return resolve(name, **kwargs)

    monkeypatch.setattr(model_store, "resolve_bundle_dir", resolve_prepared)
    monkeypatch.setattr(tiktoken, "get_encoding", lambda name: None)
    return home, sources


def test_preparation_materializes_bundles_and_does_not_borrow_global_corpora(
    prepared_sources, tmp_path, monkeypatch
):
    home, sources = prepared_sources
    foreign = tmp_path / "global-nltk"
    foreign.mkdir()
    (foreign / "corpus").write_bytes(b"global-only")
    monkeypatch.setattr(nltk.data, "path", [str(foreign)])

    def prepare_corpus(*, data_dir, offline):
        if any((Path(path) / "corpus").exists() for path in nltk.data.path):
            return
        target = Path(data_dir)
        target.mkdir(parents=True)
        (target / "corpus").write_bytes(b"transferable-corpus")

    monkeypatch.setattr(nltk_manager, "ensure_nltk_data", prepare_corpus)
    main([])
    for name, source in sources.items():
        for artifact in source.iterdir():
            assert (home / name / artifact.name).read_bytes() == artifact.read_bytes()
    assert (home / "nltk_data" / "corpus").read_bytes() == b"transferable-corpus"
    assert nltk.data.path == [str(foreign)]


def test_incomplete_bundle_cannot_be_marked_prepared(prepared_sources):
    _, sources = prepared_sources
    (sources["vision"] / "det.onnx").unlink()
    with pytest.raises(RuntimeError, match="det.onnx"):
        main([])


def test_corpus_failure_restores_callers_search_path(prepared_sources, monkeypatch):
    before = nltk.data.path[:]

    def unavailable_corpus(**kwargs):
        raise OSError("Prepared corpus unavailable")

    monkeypatch.setattr(nltk_manager, "ensure_nltk_data", unavailable_corpus)
    with pytest.raises(OSError, match="corpus unavailable"):
        main([])
    assert nltk.data.path == before


def test_tokenizers_only_uses_configured_cache_without_resolving_models(
    tmp_path, monkeypatch
):
    cache_dir = tmp_path / "prepared-tokenizers"
    monkeypatch.delenv("DEEPDOC_MODEL_HOME", raising=False)
    monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)
    monkeypatch.setenv("DEEPDOC_TIKTOKEN_CACHE_DIR", str(cache_dir))

    def forbidden_models(*args, **kwargs):
        pytest.fail("Tokenizer-only preparation attempted model resolution")

    def materialize_encoding(name):
        cache = Path(os.environ["TIKTOKEN_CACHE_DIR"])
        assert cache.is_relative_to(tmp_path)
        cache.mkdir(parents=True, exist_ok=True)
        (cache / name).write_bytes(b"prepared-tokenizer")

    monkeypatch.setattr(model_store, "resolve_bundle_dir", forbidden_models)
    monkeypatch.setattr(tiktoken, "get_encoding", materialize_encoding)
    main(["--tokenizers-only"])
    assert (cache_dir / "cl100k_base").read_bytes() == b"prepared-tokenizer"
    assert not list(tmp_path.rglob("vision"))


def test_explicit_empty_cache_is_not_silently_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPDOC_MODEL_HOME", str(tmp_path))
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", " ")
    with pytest.raises(ValueError, match="TIKTOKEN_CACHE_DIR is empty"):
        main(["--tokenizers-only"])
