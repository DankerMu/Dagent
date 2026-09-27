"""Tests for the /v1/templates SDK endpoints.

Covers a gap left by PR #1127: the v1 schemas (`V1TemplateSummary`,
`V1TemplateDetail`) had no `type` field and always populated `agent_config`,
so an external SDK caller hitting a workforce-type template would get the
same "published agent with empty instructions" outcome the internal API's
`type` gate was added to prevent - just with no way to even detect it from
the response shape.
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from xagent.templates.manager import TemplateManager
from xagent.web.api.v1.deps import (
    PersonalApiKeySnapshot,
    UserPrincipalSnapshot,
    get_user_from_personal_key,
)
from xagent.web.api.v1.templates import router as v1_templates_router
from xagent.web.models.database import Base, get_db
from xagent.web.models.public_mcp import PublicMCPApp


@pytest.fixture()
def templates_dir(tmp_path):
    (tmp_path / "plain_agent.yaml").write_text(
        """
id: plain_agent
name: Plain Agent
category: Support
descriptions:
  en: A normal single-agent template.
author: Xagent
version: "1.0"

agent_config:
  instructions: |
    You help with things.
  skills: []
  tool_categories: []
"""
    )
    (tmp_path / "ga_analyzer.yaml").write_text(
        """
id: ga_analyzer
name: GA Analyzer
category: Marketing
descriptions:
  en: Explains GA4 trends.
author: Xagent
version: "1.0"

agent_config:
  instructions: |
    You are the GA Analyzer.
  skills: []
  tool_categories: []
"""
    )
    (tmp_path / "growth_workforce.yaml").write_text(
        """
id: growth_workforce
name: Growth Marketing Workforce
category: Marketing
type: workforce
descriptions:
  en: Orchestrates GA Analyzer.
author: Xagent
version: "1.0"

workforce_config:
  manager:
    name: Growth Marketing Manager
    instructions: |
      You are the Growth Marketing Manager.
  agents:
  - template_id: ga_analyzer
    name: GA Analyzer
    assignment_instructions: Produce a Signals for Ads table.
"""
    )
    return tmp_path


@pytest.fixture()
def test_app(templates_dir, tmp_path):
    template_manager = TemplateManager(templates_root=templates_dir)
    asyncio.run(template_manager.initialize())

    engine = create_engine(f"sqlite:///{tmp_path / 'sdk-templates.db'}")
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.state.template_manager = template_manager
    app.state.test_db_engine = engine
    app.include_router(v1_templates_router, prefix="/v1")
    app.dependency_overrides[get_user_from_personal_key] = lambda: (
        UserPrincipalSnapshot(id=1, username="sdk-user", email=None, is_admin=False),
        PersonalApiKeySnapshot(key_prefix="xa_test"),
    )

    def db_session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = db_session
    yield app
    engine.dispose()


@pytest.fixture()
def client(test_app):
    return TestClient(test_app)


def test_retired_catalog_connection_is_hidden_in_sdk_templates(client, test_app):
    template = test_app.state.template_manager._templates_cache["plain_agent"]
    template["connections"] = [{"name": "Gmail"}, {"name": "LAN Files"}]
    template["agent_config"]["tool_categories"] = ["mcp:Gmail", "mcp:LAN Files"]
    with Session(test_app.state.test_db_engine) as db:
        db.add(PublicMCPApp(app_id="gmail", name="Gmail", transport="stdio"))
        db.commit()

    summary = client.get("/v1/templates", headers={"Authorization": "Bearer x"})
    detail = client.get(
        "/v1/templates/plain_agent", headers={"Authorization": "Bearer x"}
    )
    assert summary.status_code == detail.status_code == 200
    plain = next(item for item in summary.json() if item["id"] == "plain_agent")
    assert [connection["name"] for connection in plain["connections"]] == ["LAN Files"]
    assert detail.json()["agent_config"]["tool_categories"] == ["mcp:LAN Files"]


def test_list_includes_type_defaulting_to_agent(client):
    response = client.get("/v1/templates", headers={"Authorization": "Bearer x"})
    assert response.status_code == 200, response.text
    by_id = {item["id"]: item for item in response.json()}

    assert by_id["plain_agent"]["type"] == "agent"
    assert by_id["ga_analyzer"]["type"] == "agent"
    assert by_id["growth_workforce"]["type"] == "workforce"


def test_agent_template_detail_has_agent_config(client):
    response = client.get(
        "/v1/templates/plain_agent", headers={"Authorization": "Bearer x"}
    )
    assert response.status_code == 200, response.text
    detail = response.json()

    assert detail["type"] == "agent"
    assert "you help with things" in detail["agent_config"]["instructions"].lower()


def test_workforce_template_detail_has_null_agent_config(client):
    """Before this fix, agent_config was always `template.get("agent_config")
    or {}` - a workforce template's enriched agent_config is None, so this
    would have silently coerced to `{}` (a populated-looking but empty
    config) instead of the caller being able to tell from the response that
    this template isn't a single-agent template at all."""
    response = client.get(
        "/v1/templates/growth_workforce", headers={"Authorization": "Bearer x"}
    )
    assert response.status_code == 200, response.text
    detail = response.json()

    assert detail["type"] == "workforce"
    assert detail["agent_config"] is None
