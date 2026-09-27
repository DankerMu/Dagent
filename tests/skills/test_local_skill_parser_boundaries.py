"""Local skill bundles must preserve safe routing metadata and reject missing content."""

import pytest

from xagent.skills.parser import SkillParser


def test_bundle_missing_skill_document_is_rejected() -> None:
    with pytest.raises(ValueError, match="SKILL.md not found in imported-skill"):
        SkillParser.parse_bundle(
            name="imported-skill", files={"template.md": b"ignored"}
        )


def test_invalid_yaml_frontmatter_does_not_hide_body_metadata() -> None:
    skill = SkillParser.parse_bundle(
        name="local-review",
        files={
            "SKILL.md": (
                b"---\ndescription: [unclosed\n---\n"
                b"## Description\nReview local changes safely.\n"
                b"## When to Use\nBefore a release.\n"
            ),
        },
    )
    assert skill["description"] == "Review local changes safely."
    assert skill["when_to_use"] == "Before a release."


def test_directory_parse_discovers_nested_reference_files(tmp_path) -> None:
    skill_dir = tmp_path / "local-review"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("## Description\nRead refs.\n")
    (skill_dir / "references" / "guide.md").write_text("local guide")

    skill = SkillParser.parse(skill_dir)
    assert skill["files"] == ["SKILL.md", "references/guide.md"]
    assert skill["content"] == "## Description\nRead refs.\n"
