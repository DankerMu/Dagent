"""Local MCP server configuration accepts shell and JSON forms without losing values."""

import pytest

from xagent.web.api.mcp import MCPServerCreate, _build_server_config


def test_local_stdio_args_preserve_quoted_paths_and_environment_values():
    request = MCPServerCreate(
        name="local-index",
        transport="stdio",
        config={
            "command": "python",
            "args": '--index "/srv/team documents" --read-only',
            "env": "INDEX_ROOT=/srv/team MODE=read-only",
        },
    )

    built = _build_server_config(request)
    assert built.args == ["--index", "/srv/team documents", "--read-only"]
    assert built.env == {"INDEX_ROOT": "/srv/team", "MODE": "read-only"}


def test_http_header_json_preserves_values_with_spaces_and_equals():
    request = MCPServerCreate(
        name="local-gateway",
        transport="streamable_http",
        config={
            "url": "http://127.0.0.1:8765/mcp",
            "headers": '{"X-Team": "local instance", "X-Mode": "a=b"}',
        },
    )

    built = _build_server_config(request)
    assert built.headers == {"X-Team": "local instance", "X-Mode": "a=b"}
    assert built.url == "http://127.0.0.1:8765/mcp"


def test_missing_transport_target_is_rejected_before_server_creation():
    with pytest.raises(ValueError, match="requires field 'command'"):
        _build_server_config(
            MCPServerCreate(name="not-runnable", transport="stdio", config={})
        )
    with pytest.raises(ValueError, match="Unsupported MCP transport"):
        _build_server_config(
            MCPServerCreate(name="not-runnable", transport="unknown", config={})
        )
