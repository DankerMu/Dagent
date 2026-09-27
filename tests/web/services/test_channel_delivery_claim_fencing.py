"""Final delivery fences stale claims and honors transport discards."""

from unittest.mock import AsyncMock

import pytest

from tests.web.services import channel_delivery_shared as fixtures
from xagent.web.models.database import get_session_local
from xagent.web.models.task_channel_delivery import TaskChannelDelivery
from xagent.web.services import channel_delivery as delivery

accepted = fixtures.accepted
database_url = fixtures.database_url
selected = fixtures.selected


def test_replaced_delivery_claim_cannot_renew_or_retain_old_lease(accepted):
    fixtures.complete(accepted)
    original, _ = delivery._claim(accepted)
    fixtures.expire_claim(accepted)
    replacement, _ = delivery._claim(accepted)

    assert delivery._renew(original) is False
    assert delivery._renew(replacement) is True
    with get_session_local()() as db:
        row = db.get(TaskChannelDelivery, accepted)
        assert row.claim_token == replacement.claim_token
        assert row.status == "pending"


@pytest.mark.asyncio
async def test_transport_discard_settles_delivery_without_resending_result(accepted):
    fixtures.complete(accepted)
    sender = AsyncMock(side_effect=delivery.ChannelDeliveryDiscarded())

    assert await delivery.deliver_channel_result(accepted, sender) is False
    assert await delivery.deliver_channel_result(accepted, sender) is False
    sender.assert_awaited_once()
    with get_session_local()() as db:
        row = db.get(TaskChannelDelivery, accepted)
        assert row.status == "discarded"
        assert row.claim_token is None
