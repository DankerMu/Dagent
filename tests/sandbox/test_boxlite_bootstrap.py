"""Fail-closed native bootstrap cache checks at the CLI and service boundary."""

import fcntl
import hashlib
import json
import sqlite3
from contextlib import closing
from unittest.mock import Mock

import pytest

from xagent.sandbox import boxlite_sandbox
from xagent.sandbox.boxlite_bootstrap import (
    BOOTSTRAP_REFERENCE,
    BootstrapError,
    main,
    prepare_bootstrap,
    verify_bootstrap,
)


def test_cli_prepares_exact_home_and_is_idempotent(bootstrap_oci, tmp_path):
    home = tmp_path / "worker"
    main(["--bootstrap-oci", str(bootstrap_oci), "--home", str(home)])
    verify_bootstrap(home)
    with sqlite3.connect(home / "db" / "boxlite.db") as db:
        assert db.execute(
            "SELECT version FROM schema_version WHERE id=1"
        ).fetchone() == (8,)
        row = db.execute(
            "SELECT manifest_digest,complete FROM image_index WHERE reference=?",
            (BOOTSTRAP_REFERENCE,),
        ).fetchone()
    assert row[0].startswith("sha256:") and row[1] == 1
    main(["--bootstrap-oci", str(bootstrap_oci), "--home", str(home)])


def test_missing_bootstrap_fails_before_any_sdk_invocation(monkeypatch, tmp_path):
    construct = Mock(side_effect=AssertionError("native runtime must not initialize"))
    monkeypatch.setattr(boxlite_sandbox.boxlite, "Boxlite", construct)
    with pytest.raises(BootstrapError, match="--bootstrap-oci"):
        boxlite_sandbox.BoxliteSandboxService(
            boxlite_sandbox.MemBoxliteStore(), home_dir=str(tmp_path / "missing")
        )
    construct.assert_not_called()


@pytest.mark.parametrize("defect", ["incomplete", "missing-layer", "corrupt-config"])
def test_corrupt_bootstrap_refuses_native_runtime(
    prepared_boxlite_home, defect, monkeypatch
):
    home = prepared_boxlite_home
    with sqlite3.connect(home / "db" / "boxlite.db") as db:
        row = db.execute(
            "SELECT config_digest,layers FROM image_index WHERE reference=?",
            (BOOTSTRAP_REFERENCE,),
        ).fetchone()
        if defect == "incomplete":
            db.execute(
                "UPDATE image_index SET complete=0 WHERE reference=?",
                (BOOTSTRAP_REFERENCE,),
            )
    if defect == "missing-layer":
        digest = json.loads(row[1])[0].replace(":", "-")
        (home / "images" / "layers" / f"{digest}.tar.gz").unlink()
    if defect == "corrupt-config":
        digest = row[0].replace(":", "-")
        (home / "images" / "configs" / f"{digest}.json").write_bytes(b"corrupt")
    construct = Mock(side_effect=AssertionError("native runtime must not initialize"))
    monkeypatch.setattr(boxlite_sandbox.boxlite, "Boxlite", construct)
    with pytest.raises(BootstrapError, match="offline bootstrap"):
        boxlite_sandbox.BoxliteSandboxService(
            boxlite_sandbox.MemBoxliteStore(), home_dir=str(home)
        )
    construct.assert_not_called()


def test_schema_7_home_is_rejected_without_migration(
    bootstrap_oci, prepared_boxlite_home, monkeypatch
):
    home = prepared_boxlite_home
    database = home / "db" / "boxlite.db"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE schema_version SET version=7 WHERE id=1")
    original = {
        path.relative_to(home): path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    }
    construct = Mock(side_effect=AssertionError("native runtime must not initialize"))
    monkeypatch.setattr(boxlite_sandbox.boxlite, "Boxlite", construct)
    with pytest.raises(
        BootstrapError, match=r"SDK cache schema must be 8, found \(7,\)"
    ):
        prepare_bootstrap(bootstrap_oci, home)
    with pytest.raises(
        BootstrapError, match=r"SDK cache schema must be 8, found \(7,\)"
    ):
        boxlite_sandbox.BoxliteSandboxService(
            boxlite_sandbox.MemBoxliteStore(), home_dir=str(home)
        )
    construct.assert_not_called()
    assert {
        path.relative_to(home): path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    } == original


def test_cached_foreign_architecture_is_rejected_with_valid_digests(
    prepared_boxlite_home, monkeypatch
):
    home = prepared_boxlite_home
    with sqlite3.connect(home / "db" / "boxlite.db") as db:
        (old_manifest,) = db.execute(
            "SELECT manifest_digest FROM image_index WHERE reference=?",
            (BOOTSTRAP_REFERENCE,),
        ).fetchone()
        path = home / "images" / "manifests" / f"{old_manifest.replace(':', '-')}.json"
        manifest = json.loads(path.read_bytes())
        config = b'{"os":"linux","architecture":"unknown"}'
        config_digest = f"sha256:{hashlib.sha256(config).hexdigest()}"
        config_path = (
            home / "images" / "configs" / f"{config_digest.replace(':', '-')}.json"
        )
        config_path.write_bytes(config)
        manifest["config"]["digest"] = config_digest
        manifest["config"]["size"] = len(config)
        payload = json.dumps(manifest).encode()
        manifest_digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        (
            home / "images" / "manifests" / f"{manifest_digest.replace(':', '-')}.json"
        ).write_bytes(payload)
        db.execute(
            "UPDATE image_index SET manifest_digest=?, config_digest=? WHERE reference=?",
            (manifest_digest, config_digest, BOOTSTRAP_REFERENCE),
        )
    construct = Mock(side_effect=AssertionError("native runtime must not initialize"))
    monkeypatch.setattr(boxlite_sandbox.boxlite, "Boxlite", construct)
    with pytest.raises(BootstrapError, match="config platform does not match host"):
        boxlite_sandbox.BoxliteSandboxService(
            boxlite_sandbox.MemBoxliteStore(), home_dir=str(home)
        )
    construct.assert_not_called()


def test_preparation_rejects_nonempty_unprepared_home(bootstrap_oci, tmp_path):
    home = tmp_path / "occupied"
    home.mkdir()
    (home / "existing").write_text("owner data")
    with pytest.raises(BootstrapError, match="SDK cache database"):
        prepare_bootstrap(bootstrap_oci, home)
    assert (home / "existing").read_text() == "owner data"


def test_preparation_refuses_existing_guest_state(bootstrap_oci, prepared_boxlite_home):
    home = prepared_boxlite_home
    with sqlite3.connect(home / "db" / "boxlite.db") as db:
        db.execute(
            "INSERT INTO box_config(id,name,created_at,json) VALUES (?,?,?,?)",
            ("guest-id", "guest-name", 0, "{}"),
        )
    with pytest.raises(BootstrapError, match="contains boxes"):
        prepare_bootstrap(bootstrap_oci, home)


def test_preparation_refuses_locked_home(bootstrap_oci, prepared_boxlite_home):
    lock = prepared_boxlite_home / ".lock"
    with lock.open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BootstrapError, match="locked by an active runtime"):
            prepare_bootstrap(bootstrap_oci, prepared_boxlite_home)


def test_wrong_sdk_version_rejected_before_runtime(monkeypatch, prepared_boxlite_home):
    from xagent.sandbox import boxlite_bootstrap

    monkeypatch.setattr(
        boxlite_bootstrap.importlib.metadata, "version", lambda name: "0.7.5"
    )
    construct = Mock(side_effect=AssertionError("native runtime must not initialize"))
    monkeypatch.setattr(boxlite_sandbox.boxlite, "Boxlite", construct)
    with pytest.raises(BootstrapError, match="installed 0.7.5"):
        boxlite_sandbox.BoxliteSandboxService(
            boxlite_sandbox.MemBoxliteStore(), home_dir=str(prepared_boxlite_home)
        )
    construct.assert_not_called()


def test_oci_index_rejects_foreign_platform_before_creating_home(
    bootstrap_oci, tmp_path
):
    index_path = bootstrap_oci / "index.json"
    index = json.loads(index_path.read_text())
    nested = index["manifests"][0]
    nested_path = bootstrap_oci / "blobs" / "sha256" / nested["digest"][7:]
    nested_index = json.loads(nested_path.read_text())
    nested_index["manifests"][0]["platform"]["architecture"] = "unknown"
    changed = json.dumps(nested_index).encode()
    digest = hashlib.sha256(changed).hexdigest()
    (bootstrap_oci / "blobs" / "sha256" / digest).write_bytes(changed)
    nested.update(digest=f"sha256:{digest}", size=len(changed))
    index_path.write_text(json.dumps(index))
    home = tmp_path / "new-home"
    with pytest.raises(BootstrapError, match="no unique linux/"):
        prepare_bootstrap(bootstrap_oci, home)
    assert not home.exists()


def test_preparation_rejects_wrong_oci_blob_before_creating_home(
    bootstrap_oci, tmp_path
):
    source = next((bootstrap_oci / "blobs" / "sha256").iterdir())
    source.write_bytes(b"not the descriptor's digest")
    home = tmp_path / "new-home"
    with pytest.raises(BootstrapError, match="SHA256"):
        prepare_bootstrap(bootstrap_oci, home)
    assert not home.exists()
