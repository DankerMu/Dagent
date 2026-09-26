"""Withholding image tools must surface operator diagnostics at INFO."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from xagent.core.model.image.base import BaseImageModel
from xagent.core.tools.adapters.vibe.image_tool import ImageGenerationTool


@pytest.fixture
def mock_workspace(tmp_path):
    workspace = Mock()
    workspace.output_dir = tmp_path / "output"
    workspace.output_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def auto_register_files():
        yield workspace

    workspace.auto_register_files = auto_register_files
    workspace.get_file_id_from_path = Mock(return_value="test-file-id")
    return workspace


def _models_with_abilities(abilities: list[str]) -> dict[str, Mock]:
    model = Mock(spec=BaseImageModel)
    model.abilities = list(abilities)
    model.has_ability = Mock(side_effect=lambda ability: ability in abilities)
    return {"model1": model}


def test_withholding_edit_reports_generate_only_abilities_at_info(
    mock_workspace, caplog
):
    """A generate-only deployment withholds edit_image; the operator log must
    name that model and that generate is available while edit is not.

    The INFO call is gated on logger.isEnabledFor, so a default WARNING
    logger (CI) never emits it. Forcing INFO makes the diagnostic path
    deterministic rather than environment-dependent.
    """
    logger = logging.getLogger("xagent.core.tools.adapters.vibe.image_tool")
    tool = ImageGenerationTool(
        _models_with_abilities(["generate"]), workspace=mock_workspace
    )

    with caplog.at_level(logging.INFO, logger=logger.name):
        tools = tool.get_tools()

    names = {candidate.metadata.name for candidate in tools}
    assert "generate_image" in names
    assert "edit_image" not in names

    info_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == logger.name and record.levelno == logging.INFO
    ]
    assert info_messages, "withholding a tool must emit an INFO diagnostic"
    diagnostic = info_messages[0]
    assert "model1" in diagnostic
    assert "generate" in diagnostic
    assert "False" in diagnostic
