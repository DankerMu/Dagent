"""Local identity, token, key and database failure boundaries."""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, literal, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from xagent.builtin_identity import (
    builtin_provenance_identity,
    canonicalize_builtin_identity,
)
from xagent.core.utils.encryption import (
    decrypt_value,
    derive_secret_hmac,
    get_cipher,
)
from xagent.web import auth_config
from xagent.web.api.auth import create_access_token, verify_refresh_token, verify_token
from xagent.web.models import database


def test_builtin_names_cannot_collide_after_case_and_whitespace_normalization():
    assert canonicalize_builtin_identity("  LAN   Model  ") == "lan-model"
    assert canonicalize_builtin_identity(None) is None
    assert builtin_provenance_identity({"registry": "local", "app_id": "image"}) == (
        "local",
        "image",
    )
    assert builtin_provenance_identity({"registry": "local", "app_id": ""}) is None
    assert builtin_provenance_identity({"registry": 7, "app_id": "image"}) is None


def test_access_token_rejects_tampering_and_cannot_rotate_as_refresh():
    access = create_access_token({"user_id": 17, "sub": "local-user"})
    assert verify_token(access)["user_id"] == 17
    assert verify_refresh_token(access) is None
    # Change a signed payload segment, rather than the last base64 character:
    header, payload, signature = access.split(".")
    altered = f"{header}.{payload[:-2]}{'A' if payload[-2] != 'A' else 'B'}{payload[-1]}.{signature}"
    assert verify_token(altered) is None
    assert verify_refresh_token(altered) is None


def test_local_token_lifetime_rejects_nonpositive_or_malformed_overrides(monkeypatch):
    name = "XAGENT_ACCESS_TOKEN_EXPIRE_MINUTES"
    for value in ("0", "-30", "not-an-integer"):
        monkeypatch.setenv(name, value)
        assert auth_config._get_positive_int_from_env(name, 120) == 120
    monkeypatch.setenv(name, "45")
    assert auth_config._get_positive_int_from_env(name, 120) == 45


def test_hmac_requires_a_purpose_before_processing_a_secret():
    with pytest.raises(ValueError):
        derive_secret_hmac("local-password", purpose=b"")


def test_unreadable_token_is_never_decrypted_as_plaintext_when_key_is_broken(
    monkeypatch,
):
    ciphertext = Fernet(Fernet.generate_key()).encrypt(b"local-secret").decode()
    monkeypatch.setenv("ENCRYPTION_KEY", "invalid-key")
    get_cipher.cache_clear()
    try:
        assert decrypt_value(ciphertext) == ciphertext
    finally:
        get_cipher.cache_clear()


def test_database_session_requires_configuration_and_readonly_sqlite_never_creates_file(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(database, "_SessionLocal", None)
    with pytest.raises(RuntimeError, match="not initialized"):
        next(database.get_db())

    missing = tmp_path / "does-not-exist.sqlite"
    previous_engine = database._engine
    previous_factory = database._SessionLocal
    try:
        database.configure_db(f"sqlite:///{missing}", read_only=True)
        with pytest.raises(OperationalError):
            with database.get_engine().connect() as connection:
                connection.exec_driver_sql("SELECT 1")
        assert not missing.exists()
        assert (
            database._sqlite_read_only_url("sqlite:///:memory:") == "sqlite:///:memory:"
        )
    finally:
        database._engine.dispose()
        database._engine = previous_engine
        database._SessionLocal = previous_factory


def test_connection_release_reports_failed_rollback_without_discarding_session(
    monkeypatch,
):
    engine = create_engine("sqlite:///:memory:")
    db = Session(engine)
    try:
        db.execute(select(literal(1)))
        assert db.in_transaction()

        def failed_rollback():
            raise OSError("database connection lost")

        monkeypatch.setattr(db, "rollback", failed_rollback)
        assert database.release_db_connection_if_clean(db) is False
        assert db.in_transaction()
    finally:
        db.close()
        engine.dispose()
