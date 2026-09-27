"""Channel configuration lifecycle at the database-backed transport seam."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from xagent.web.models.database import Base
from xagent.web.models.user import User
from xagent.web.models.user_channel import UserChannel
from xagent.web.services import channel_runtime


@pytest.fixture
def channels(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'channels.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(channel_runtime, "get_session_local", lambda: sessions)
    with sessions() as db:
        owner = User(username="local-channel-owner", password_hash="unused")
        db.add(owner)
        db.flush()
        for name, config, active in (
            ("ready", {"access_key": "local-key", "region": "local"}, True),
            ("incomplete", {"region": "missing-key"}, True),
            ("disabled", {"access_key": "do-not-leak"}, False),
        ):
            db.add(
                UserChannel(
                    user_id=owner.id,
                    channel_type="local",
                    channel_name=name,
                    config=config,
                    is_active=active,
                )
            )
        db.commit()
        ids = {row.channel_name: row.id for row in db.query(UserChannel).all()}
    yield sessions, ids
    engine.dispose()


@pytest.mark.asyncio
async def test_only_complete_active_channel_credentials_are_advertised(channels):
    _, ids = channels
    configs = await channel_runtime.load_active_channel_configs(
        channel_type="local",
        required_config_keys=("access_key",),
        optional_config_keys=("region",),
    )

    assert len(configs) == 1
    assert configs[0].channel_id == ids["ready"]
    assert configs[0].config_value("access_key") == "local-key"
    assert configs[0].config_value("region") == "local"


@pytest.mark.asyncio
async def test_revoking_credentials_hides_channel_from_sender_and_future_discovery(
    channels,
):
    sessions, ids = channels
    channel_id = ids["ready"]
    assert channel_runtime.deactivate_channel_sync(
        channel_id=channel_id, clear_config_keys=("access_key",)
    )
    with sessions() as db:
        channel = db.get(UserChannel, channel_id)
        assert channel.is_active is False
        assert channel.config == {"region": "local"}
    assert (
        channel_runtime.deactivate_channel_sync(
            channel_id=channel_id, clear_config_keys=("access_key",)
        )
        is False
    )
    with pytest.raises(channel_runtime.ChannelConfigurationError):
        await channel_runtime.authorize_channel_sender(
            channel_id=channel_id, external_user_id="local-sender"
        )
    assert (
        await channel_runtime.load_active_channel_configs(
            channel_type="local", required_config_keys=("access_key",)
        )
        == ()
    )
    assert channel_runtime.deactivate_channel_sync(channel_id=999999) is False
