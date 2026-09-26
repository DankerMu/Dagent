"""Frozen debt cannot be relabeled or seeded from candidate-only controls."""

import json

import pytest

from scripts.engineering.check import main
from scripts.engineering.findings import make_finding, serialize_baseline
from tests.engineering.conftest import git_init_and_commit, write_constraints
from tests.engineering.test_baseline import _stub_collectors, _write_baseline


@pytest.mark.parametrize("command", ["guardrails", "security", "secrets", "baseline"])
@pytest.mark.parametrize("reference", [None, "other-reference"])
def test_every_gate_rejects_relabelled_baseline(
    clean_repo, monkeypatch, command, reference
):
    write_constraints(clean_repo, **{"baseline.rev": "abc123"})
    artifact = _write_baseline(clean_repo, [])
    _stub_collectors(monkeypatch)
    monkeypatch.setattr(
        "scripts.engineering.guardrails.collect_guardrail_findings", lambda root: []
    )
    monkeypatch.setattr(
        "scripts.engineering.security.collect_security_findings",
        lambda root, **kwargs: [],
    )
    args = ["--root", str(clean_repo), command]
    assert main(args) == 0
    payload = json.loads(artifact.read_text())
    if reference is None:
        payload.pop("reference")
    else:
        payload["reference"] = reference
    artifact.write_text(json.dumps(payload))
    assert main(args) != 0


def test_capture_excludes_only_nonhistorical_injected_controls(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "src/xagent/app.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('historical')\n")
    reference = git_init_and_commit(tmp_path)
    write_constraints(tmp_path, **{"baseline.rev": reference})
    historical = make_finding(
        check="secret",
        path="src/xagent/app.py",
        line=1,
        detector="fixture",
        severity="error",
        identity="historical",
        detail="historical finding",
    )
    candidate = make_finding(
        check="secret",
        path="constraints.yaml",
        line=1,
        detector="fixture",
        severity="error",
        identity="candidate",
        detail="candidate finding",
    )
    _stub_collectors(monkeypatch, [historical, candidate])
    assert (
        main(["--root", str(tmp_path), "baseline", "--write", "--reference", reference])
        == 0
    )
    captured = json.loads((tmp_path / ".engineering/baseline.json").read_text())
    assert {item["path"] for item in captured["findings"]} == {"src/xagent/app.py"}
    assert main(["--root", str(tmp_path), "baseline"]) == 1
    assert "constraints.yaml:1  new secret finding" in capsys.readouterr().err


def test_capture_identity_is_independent_of_detector_order():
    first = make_finding(
        check="size",
        path="src/a.py",
        line=1,
        detector="fixture",
        severity="error",
        identity="a",
        detail="a",
    )
    second = make_finding(
        check="size",
        path="src/b.py",
        line=1,
        detector="fixture",
        severity="error",
        identity="b",
        detail="b",
    )
    forward = serialize_baseline(
        reference="abc123", tool_identities={}, findings=[first, second]
    )
    reverse = serialize_baseline(
        reference="abc123", tool_identities={}, findings=[second, first]
    )
    assert json.dumps(forward, sort_keys=True) == json.dumps(reverse, sort_keys=True)
    other_severity = make_finding(
        check="size",
        path="src/a.py",
        line=1,
        detector="fixture",
        severity="low",
        identity="a",
        detail="same fingerprint, lower severity",
    )
    colliding = serialize_baseline(
        reference="abc123", tool_identities={}, findings=[first, other_severity]
    )
    reversed_collision = serialize_baseline(
        reference="abc123", tool_identities={}, findings=[other_severity, first]
    )
    assert json.dumps(colliding, sort_keys=True) == json.dumps(
        reversed_collision, sort_keys=True
    )


@pytest.mark.parametrize("command", ["guardrails", "security", "secrets", "baseline"])
def test_frozen_artifact_cannot_add_finding_at_same_reference(
    git_repo, monkeypatch, capsys, command
):
    root, pristine = git_repo
    write_constraints(root, **{"baseline.rev": pristine})
    artifact = _write_baseline(root, [])
    base = git_init_and_commit(root, "freeze baseline artifact")
    _stub_collectors(monkeypatch)
    monkeypatch.setattr(
        "scripts.engineering.guardrails.collect_guardrail_findings", lambda root: []
    )
    monkeypatch.setattr(
        "scripts.engineering.security.collect_security_findings",
        lambda root, **kwargs: [],
    )
    args = ["--root", str(root), "--base", base, command]
    before = main(args)
    output = capsys.readouterr()
    if command == "baseline":
        assert before == 1
        assert "does not match --base" in output.err
    else:
        assert before == 0
    payload = json.loads(artifact.read_text())
    forged = make_finding(
        check="size",
        path="src/xagent/app.py",
        line=1,
        detector="fixture",
        severity="error",
        identity="forged",
        detail="forged debt",
    )
    payload["findings"].append(forged.as_dict())
    payload["counts"]["size"] = 1
    artifact.write_text(json.dumps(payload))
    assert main(args) == 1
    assert "differs from --base" in capsys.readouterr().err
