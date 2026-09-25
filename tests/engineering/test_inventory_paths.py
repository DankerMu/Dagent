"""Source inventories reject external targets with a named gate error."""

import json

import pytest

from scripts.engineering.check import main


@pytest.mark.parametrize("command", ["guardrails", "coverage"])
def test_root_escaping_source_link_has_protocol_error(clean_repo, capsys, command):
    outside = clean_repo.parent / f"{clean_repo.name}-external.py"
    outside.write_text("pass\n")
    link = clean_repo / "src/xagent/escape.py"
    link.symlink_to(outside)
    args = ["--root", str(clean_repo), command]
    if command == "coverage":
        report = clean_repo / "coverage.json"
        report.write_text(json.dumps({"files": {}}))
        args += ["--scope", "python", "--pytest-json", str(report)]
    try:
        assert main(args) == 2
        captured = capsys.readouterr()
        assert "escape.py" in captured.err
        assert "Traceback" not in captured.err
    finally:
        outside.unlink()
