"""A malformed local template must not break the rest of the catalog."""

import pytest

from xagent.templates.manager import TemplateManager


@pytest.mark.asyncio
async def test_unsupported_template_type_is_excluded_without_hiding_valid_agent(
    tmp_path,
):
    (tmp_path / "unsupported.yaml").write_text(
        "id: unsupported\nname: Unsupported\ncategory: Local\n"
        "descriptions:\n  en: Invalid template type\ntype: unknown-kind\n"
    )
    (tmp_path / "assistant.yaml").write_text(
        "id: assistant\nname: Local Assistant\ncategory: Local\n"
        "descriptions:\n  en: A usable local assistant\n"
    )
    manager = TemplateManager(templates_root=tmp_path)

    assert await manager.get_template("unsupported") is None
    assistant = await manager.get_template("assistant")
    assert assistant is not None
    assert assistant["name"] == "Local Assistant"
