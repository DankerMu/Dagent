"""Fail-closed native bootstrap cache checks at the CLI and service boundary."""

import json
import sqlite3
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
        ).fetchone() == (7,)
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


@pytest.mark.parametrize(
    "defect", ["incomplete", "schema", "missing-layer", "corrupt-config", "wrong-arch"]
)
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
        if defect == "schema":
            db.execute("UPDATE schema_version SET version=6 WHERE id=1")
    if defect == "missing-layer":
        digest = json.loads(row[1])[0].replace(":", "-")
        (home / "images" / "layers" / f"{digest}.tar.gz").unlink()
    if defect == "corrupt-config":
        digest = row[0].replace(":", "-")
        (home / "images" / "configs" / f"{digest}.json").write_bytes(b"corrupt")
    if defect == "wrong-arch":
        digest = row[0].replace(":", "-")
        (home / "images" / "configs" / f"{digest}.json").write_bytes(
            b'{"os":"linux","architecture":"invalid"}'
        )
    construct = Mock(side_effect=AssertionError("native runtime must not initialize"))
    monkeypatch.setattr(boxlite_sandbox.boxlite, "Boxlite", construct)
    with pytest.raises(BootstrapError, match="offline bootstrap"):
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


def test_wrong_sdk_version_rejected_before_runtime(monkeypatch, prepared_boxlite_home):
    from xagent.sandbox import boxlite_bootstrap

    monkeypatch.setattr(
        boxlite_bootstrap.importlib.metadata, "version", lambda name: "0.7.6"
    )
    construct = Mock(side_effect=AssertionError("native runtime must not initialize"))
    monkeypatch.setattr(boxlite_sandbox.boxlite, "Boxlite", construct)
    with pytest.raises(BootstrapError, match="installed 0.7.6"):
        boxlite_sandbox.BoxliteSandboxService(
            boxlite_sandbox.MemBoxliteStore(), home_dir=str(prepared_boxlite_home)
        )
    construct.assert_not_called()


def test_oci_index_rejects_foreign_platform_before_creating_home(
    bootstrap_oci, tmp_path
):
    import hashlib

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
