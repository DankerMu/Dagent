"""Configuration compatibility across old plaintext and encrypted channel rows."""

from cryptography.fernet import Fernet

from xagent.config import DEV_FALLBACK_ENCRYPTION_KEY
from xagent.web.models.user_channel import (
    UserChannel,
    has_production_channel_encryption_key,
)


def test_channel_config_reads_legacy_plaintext_and_does_not_mutate_saved_json(
    monkeypatch,
):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    saved = {
        "allowed_users": ["alice"],
        "app_secret": "legacy-secret",  # pragma: allowlist secret - synthetic legacy row
    }
    channel = UserChannel(
        user_id=1, channel_type="local", channel_name="Legacy", _config=saved
    )

    config = channel.config
    assert config == saved
    config["allowed_users"].append("mallory")
    config["app_secret"] = "modified"  # pragma: allowlist secret - synthetic mutation
    assert channel.config == saved
    assert saved["allowed_users"] == ["alice"]
    assert saved["app_secret"] == "legacy-secret"  # pragma: allowlist secret


def test_channel_config_encrypts_secrets_without_double_encrypting_or_mutating_input(
    monkeypatch,
):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    cipher = Fernet(DEV_FALLBACK_ENCRYPTION_KEY.encode())
    already_encrypted = cipher.encrypt(b"old-token").decode()
    original = {
        "bot_token": already_encrypted,
        "app_secret": "new-secret",  # pragma: allowlist secret - synthetic encryption input
        "app_token": "workspace-token",
        "allowed_users": ["alice"],
    }
    channel = UserChannel(user_id=1, channel_type="local", channel_name="Local")

    channel.config = original

    assert original["app_secret"] == "new-secret"  # pragma: allowlist secret
    assert channel._config["bot_token"] == already_encrypted
    assert channel._config["app_secret"] != "new-secret"  # pragma: allowlist secret
    assert channel._config["app_token"] != "workspace-token"
    assert channel.config == {
        "bot_token": "old-token",
        "app_secret": "new-secret",  # pragma: allowlist secret - synthetic input
        "app_token": "workspace-token",
        "allowed_users": ["alice"],
    }
    original["allowed_users"].append("mallory")
    assert channel.config["allowed_users"] == ["alice"]


def test_empty_channel_config_removes_old_credentials(monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    channel = UserChannel(user_id=1, channel_type="local", channel_name="Local")
    channel.config = {
        "app_secret": "old-secret"  # pragma: allowlist secret - synthetic row
    }

    channel.config = {}

    assert channel.config == {}
    assert channel._config == {}


def test_production_channel_key_gate_rejects_dev_fallback_and_accepts_real_key(
    monkeypatch,
):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    assert has_production_channel_encryption_key() is False
    monkeypatch.setenv("ENCRYPTION_KEY", DEV_FALLBACK_ENCRYPTION_KEY)
    assert has_production_channel_encryption_key() is False
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert has_production_channel_encryption_key() is True
