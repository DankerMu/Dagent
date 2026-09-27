"""Generic HTTP MCP OAuth resolver must not resurrect stale bearer credentials."""

from datetime import datetime, timedelta, timezone

import pytest

from tests.web.tools import test_mcp_oauth_runtime as fixtures
from tests.web.tools.test_mcp_oauth_runtime import _add_mcp_oauth_server
from xagent.web.services.mcp_oauth import MCPAuthorizationChallenge
from xagent.web.tools.config import (
    ResolvedToken,
    TokenRequest,
    WebToolConfig,
    set_oauth_token_resolver_hook,
)

db_session = fixtures.db_session


@pytest.fixture(autouse=True)
def clear_resolver():
    set_oauth_token_resolver_hook(None)
    yield
    set_oauth_token_resolver_hook(None)


def config_for(db, user):
    return WebToolConfig(db=db, request=None, user=user, user_id=user.id)


def invalid_token_challenge():
    return MCPAuthorizationChallenge(
        resource_metadata_url="https://auth.example.com/.well-known/oauth-protected-resource",
        scope="records.read",
        params={},
    )


@pytest.mark.asyncio
async def test_remote_refresh_uses_next_generation_and_rejects_repeated_challenge(
    db_session,
):
    db, user, _ = db_session
    server = _add_mcp_oauth_server(db, user)
    requests: list[TokenRequest] = []

    async def resolve(request):
        requests.append(request)
        if request.refresh is None:
            return ResolvedToken(access_token="first", generation="generation-1")
        return ResolvedToken(access_token="second", generation="generation-2")

    set_oauth_token_resolver_hook(resolve)
    cfg = config_for(db, user)
    try:
        configs = await cfg.get_mcp_server_configs()
        first = configs[0]["config"]
        assert first["headers"]["Authorization"] == "Bearer first"
        refreshed = await first["_oauth_token_resolver_refresh"](
            invalid_token_challenge()
        )
        assert refreshed["headers"]["Authorization"] == "Bearer second"
        assert requests[0].provider == requests[1].provider == server.name
        assert requests[1].refresh.failed_generation == "generation-1"
        assert requests[1].refresh.challenge_scope == "records.read"
        assert (
            await refreshed["_oauth_token_resolver_refresh"](invalid_token_challenge())
            is None
        )
        assert requests[2].refresh.failed_generation == "generation-2"
    finally:
        cfg.close()


@pytest.mark.asyncio
async def test_remote_refresh_rejects_changed_resolver_without_sending_stale_token(
    db_session,
):
    db, user, _ = db_session
    _add_mcp_oauth_server(db, user)
    refresh_calls = 0

    async def resolve(request):
        nonlocal refresh_calls
        if request.refresh is not None:
            refresh_calls += 1
        return ResolvedToken(access_token="first", generation="generation-1")

    set_oauth_token_resolver_hook(resolve)
    cfg = config_for(db, user)
    try:
        configs = await cfg.get_mcp_server_configs()
        refresh = configs[0]["config"]["_oauth_token_resolver_refresh"]
        set_oauth_token_resolver_hook(resolve)
        assert await refresh(invalid_token_challenge()) is None
        assert refresh_calls == 0
    finally:
        cfg.close()


@pytest.mark.asyncio
async def test_remote_resolver_exception_does_not_fall_back_to_static_authorization(
    db_session,
):
    db, user, _ = db_session
    server = _add_mcp_oauth_server(db, user)

    async def resolve(request):
        raise RuntimeError("private-resolver-secret")

    set_oauth_token_resolver_hook(resolve)
    cfg = config_for(db, user)
    try:
        configs = await cfg.get_mcp_server_configs()
        assert configs[0]["transport"] == "unavailable"
        assert configs[0]["config"]["server_id"] == server.id
        assert "private-resolver-secret" not in repr(configs)
        assert "static-token" not in repr(configs)
    finally:
        cfg.close()


@pytest.mark.asyncio
async def test_remote_near_expiry_token_is_not_cached(db_session):
    db, user, _ = db_session
    _add_mcp_oauth_server(db, user)
    calls = 0

    async def resolve(request):
        nonlocal calls
        calls += 1
        return ResolvedToken(
            access_token=f"token-{calls}",
            generation=f"generation-{calls}",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )

    set_oauth_token_resolver_hook(resolve)
    cfg = config_for(db, user)
    try:
        first = await cfg.get_mcp_server_configs()
        second = await cfg.get_mcp_server_configs()
        assert first[0]["config"]["headers"]["Authorization"] == "Bearer token-1"
        assert second[0]["config"]["headers"]["Authorization"] == "Bearer token-2"
    finally:
        cfg.close()


@pytest.mark.asyncio
async def test_remote_resolver_rejects_non_token_result_without_static_fallback(
    db_session,
):
    db, user, _ = db_session
    server = _add_mcp_oauth_server(db, user)
    set_oauth_token_resolver_hook(lambda request: object())
    cfg = config_for(db, user)
    try:
        configs = await cfg.get_mcp_server_configs()
        assert configs[0]["transport"] == "unavailable"
        assert configs[0]["config"]["server_id"] == server.id
        assert cfg.get_mcp_oauth_diagnostics()[0]["exception_type"] == "object"
        assert "static-token" not in repr(configs)
    finally:
        cfg.close()


@pytest.mark.asyncio
async def test_remote_resolver_rejects_expired_token_without_static_fallback(
    db_session,
):
    db, user, _ = db_session
    server = _add_mcp_oauth_server(db, user)
    set_oauth_token_resolver_hook(
        lambda request: ResolvedToken(
            access_token="expired-private-token",
            generation="stale-generation",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    cfg = config_for(db, user)
    try:
        configs = await cfg.get_mcp_server_configs()
        assert configs[0]["transport"] == "unavailable"
        assert configs[0]["config"]["server_id"] == server.id
        assert cfg.get_mcp_oauth_diagnostics()[0]["exception_type"] == (
            "ExpiredAccessToken"
        )
        assert "expired-private-token" not in repr(configs)
        assert "static-token" not in repr(configs)
    finally:
        cfg.close()


@pytest.mark.asyncio
async def test_remote_refresh_reports_only_classified_reauthorization_failure(
    db_session,
):
    db, user, _ = db_session
    _add_mcp_oauth_server(db, user)

    class RefreshFailure(RuntimeError):
        oauth_token_resolver_failure_code = "oauth_token_required"

    async def resolve(request):
        if request.refresh is not None:
            raise RefreshFailure("private-refresh-error")
        return ResolvedToken(access_token="first", generation="generation-1")

    set_oauth_token_resolver_hook(resolve)
    cfg = config_for(db, user)
    try:
        configs = await cfg.get_mcp_server_configs()
        result = await configs[0]["config"]["_oauth_token_resolver_refresh"](
            invalid_token_challenge()
        )
        assert result.failure_code == "oauth_token_required"
        assert "private-refresh-error" not in repr(result)
        assert "first" not in repr(result)
    finally:
        cfg.close()


@pytest.mark.asyncio
async def test_oauth_resolver_precedes_untrusted_delegated_authorization(db_session):
    db, user, _ = db_session
    server = _add_mcp_oauth_server(db, user)
    server.runtime_bindings = [
        {
            "source": {"input_type": "secrets", "key": "authorization"},
            "target": {"target_type": "transport_headers", "key": "Authorization"},
        },
        {
            "source": {"input_type": "secrets", "key": "lan_flag"},
            "target": {"target_type": "transport_headers", "key": "X-Lan-Flag"},
        },
    ]
    server.allow_delegated_authorization = False
    db.commit()
    set_oauth_token_resolver_hook(
        lambda request: ResolvedToken(
            access_token="resolver-token",
            generation="generation-1",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    cfg = config_for(db, user)
    cfg._connector_runtime_view = {
        f"mcp:{server.id}": {
            "context": {},
            "secrets": {
                "authorization": "Bearer untrusted-token",
                "lan_flag": "runtime-attached",
            },
            "auth_selector": {},
        }
    }
    try:
        configs = await cfg.get_mcp_server_configs()
        assert configs[0]["config"]["headers"]["Authorization"] == (
            "Bearer resolver-token"
        )
        assert configs[0]["config"]["headers"]["X-Lan-Flag"] == "runtime-attached"
        assert "untrusted-token" not in repr(configs)
        assert "static-token" not in repr(configs)
    finally:
        cfg.close()


@pytest.mark.asyncio
async def test_resolver_exception_properties_cannot_leak_private_diagnostics(
    db_session,
):
    db, user, _ = db_session
    _add_mcp_oauth_server(
        db, user, resource="https://mcp.example.com/mcp?api_key=resource-private"
    )

    class ResolverFailure(RuntimeError):
        @property
        def oauth_token_resolver_failure_code(self):
            raise RuntimeError("code-private")

        @property
        def oauth_token_resolver_diagnostic_actor_id(self):
            raise RuntimeError("actor-private")

    async def resolve(request):
        raise ResolverFailure("resolver-private")

    set_oauth_token_resolver_hook(resolve)
    cfg = config_for(db, user)
    try:
        configs = await cfg.get_mcp_server_configs()
        assert configs[0]["transport"] == "unavailable"
        diagnostics = cfg.get_mcp_oauth_diagnostics()
        assert diagnostics[0]["exception_type"] == "ResolverFailure"
        assert "actor_id" not in diagnostics[0]
        public = repr(configs) + repr(diagnostics)
        for secret in (
            "code-private",
            "actor-private",
            "resource-private",
            "resolver-private",
        ):
            assert secret not in public
    finally:
        cfg.close()


@pytest.mark.asyncio
async def test_remote_token_cache_reloads_after_resolver_reregistration(db_session):
    db, user, _ = db_session
    _add_mcp_oauth_server(db, user)
    issued = 0

    def resolve(request):
        nonlocal issued
        issued += 1
        return ResolvedToken(
            access_token=f"issued-{issued}",
            generation=f"generation-{issued}",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    set_oauth_token_resolver_hook(resolve)
    cfg = config_for(db, user)
    try:
        first = await cfg.get_mcp_server_configs()
        cached = await cfg.get_mcp_server_configs()
        assert issued == 1
        assert first[0]["config"]["headers"]["Authorization"] == "Bearer issued-1"
        assert cached[0]["config"]["headers"]["Authorization"] == "Bearer issued-1"

        set_oauth_token_resolver_hook(resolve)
        rotated = await cfg.get_mcp_server_configs()
        assert issued == 2
        assert rotated[0]["config"]["headers"]["Authorization"] == "Bearer issued-2"
    finally:
        cfg.close()


@pytest.mark.asyncio
async def test_remote_resolver_failure_reloads_instead_of_caching_unavailable(
    db_session,
):
    db, user, _ = db_session
    _add_mcp_oauth_server(db, user)
    calls = 0

    def resolve(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary-resolver-secret")
        return ResolvedToken(
            access_token="recovered-token",
            generation="generation-2",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    set_oauth_token_resolver_hook(resolve)
    cfg = config_for(db, user)
    try:
        unavailable = await cfg.get_mcp_server_configs()
        available = await cfg.get_mcp_server_configs()
        assert calls == 2
        assert unavailable[0]["transport"] == "unavailable"
        assert available[0]["config"]["headers"]["Authorization"] == (
            "Bearer recovered-token"
        )
        assert "temporary-resolver-secret" not in repr(unavailable)
    finally:
        cfg.close()


@pytest.mark.asyncio
async def test_remote_refresh_rejects_malformed_token_without_reusing_old_bearer(
    db_session,
):
    db, user, _ = db_session
    _add_mcp_oauth_server(db, user)
    refresh_requests = 0

    def resolve(request):
        nonlocal refresh_requests
        if request.refresh is not None:
            refresh_requests += 1
            return object()
        return ResolvedToken(access_token="first", generation="generation-1")

    set_oauth_token_resolver_hook(resolve)
    cfg = config_for(db, user)
    try:
        configs = await cfg.get_mcp_server_configs()
        refreshed = await configs[0]["config"]["_oauth_token_resolver_refresh"](
            invalid_token_challenge()
        )
        assert refreshed is None
        assert refresh_requests == 1
    finally:
        cfg.close()


@pytest.mark.asyncio
async def test_remote_refresh_requires_generation_for_replay_protection(db_session):
    db, user, _ = db_session
    _add_mcp_oauth_server(db, user)
    refresh_requests = 0

    def resolve(request):
        nonlocal refresh_requests
        if request.refresh is not None:
            refresh_requests += 1
            return ResolvedToken(
                access_token="unsafe-second", generation="generation-2"
            )
        return ResolvedToken(access_token="first")

    set_oauth_token_resolver_hook(resolve)
    cfg = config_for(db, user)
    try:
        configs = await cfg.get_mcp_server_configs()
        refreshed = await configs[0]["config"]["_oauth_token_resolver_refresh"](
            invalid_token_challenge()
        )
        assert refreshed is None
        assert refresh_requests == 0
    finally:
        cfg.close()
