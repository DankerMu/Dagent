from __future__ import annotations

import json
from pathlib import Path

from scripts.engineering.check import main
from scripts.engineering.config import load_constraints
from scripts.engineering.findings import make_finding, serialize_baseline
from scripts.engineering.security import collect_security_findings
from scripts.engineering.tools import ToolFailure


def test_security_missing_tool_is_failure_not_skip(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(
        "scripts.engineering.security.collect_security_findings",
        lambda root, secrets_only=False: (_ for _ in ()).throw(
            ToolFailure("detect-secrets is not installed")
        ),
    )
    code = main(["--root", str(clean_repo), "security", "--secrets-only"])
    captured = capsys.readouterr()
    assert code == 2
    assert "tool failure" in captured.err
    assert "detect-secrets" in captured.err


def test_secrets_alias_runs_secrets_only(clean_repo: Path, capsys, monkeypatch) -> None:
    seen = {}

    def fake(root, secrets_only=False):
        seen["secrets_only"] = secrets_only
        return []

    monkeypatch.setattr("scripts.engineering.security.collect_security_findings", fake)
    monkeypatch.setattr(
        "scripts.engineering.security.load_constraints",
        lambda root: type(
            "C",
            (),
            {"baseline_artifact": ".engineering/baseline.json"},
        )(),
    )
    code = main(["--root", str(clean_repo), "secrets"])
    captured = capsys.readouterr()
    assert seen["secrets_only"] is True
    assert code == 1
    assert "unknown remains unknown" in captured.err


def test_security_new_finding_is_rejected(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    finding = make_finding(
        check="secret",
        path="src/xagent/app.py",
        line=1,
        detector="detect-secrets",
        severity="error",
        identity="Secret Keyword:abc",
        detail="Secret Keyword at src/xagent/app.py:1",
    )
    monkeypatch.setattr(
        "scripts.engineering.security.collect_security_findings",
        lambda root, secrets_only=False: [finding],
    )
    artifact = clean_repo / ".engineering" / "baseline.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        (
            '{"schema":"dagent.engineering.baseline.v1","reference":'
            f"{json.dumps(load_constraints(clean_repo).baseline_rev)},"
            '"counts":{},"findings":[]}'
        ),
        encoding="utf-8",
    )
    code = main(["--root", str(clean_repo), "security", "--secrets-only"])
    captured = capsys.readouterr()
    assert code == 1
    assert "new secret finding" in captured.err


def _write_security_baseline(root: Path, findings) -> None:
    artifact = root / ".engineering" / "baseline.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    payload = serialize_baseline(
        reference=load_constraints(root).baseline_rev,
        tool_identities={"detect-secrets": "1.5.0"},
        findings=findings,
    )
    artifact.write_text(json.dumps(payload), encoding="utf-8")


def test_security_rejects_invalid_explicit_base(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    _write_security_baseline(clean_repo, [])
    monkeypatch.setattr(
        "scripts.engineering.security.collect_security_findings",
        lambda root, secrets_only=False: [],
    )
    code = main(["--root", str(clean_repo), "--base", "missing-ref", "security"])
    captured = capsys.readouterr()
    assert code == 2
    assert "git rev-parse" in captured.err


def test_security_accepts_frozen_secret(clean_repo: Path, capsys, monkeypatch) -> None:
    finding = make_finding(
        check="secret",
        path="src/xagent/app.py",
        line=1,
        detector="detect-secrets",
        severity="error",
        identity="Secret Keyword:abc",
        detail="Secret Keyword at src/xagent/app.py:1",
    )
    _write_security_baseline(clean_repo, [finding])
    monkeypatch.setattr(
        "scripts.engineering.security.collect_security_findings",
        lambda root, secrets_only=False: [finding],
    )
    code = main(["--root", str(clean_repo), "security", "--secrets-only"])
    captured = capsys.readouterr()
    assert code == 0
    assert "detect-secrets findings within frozen baseline" in captured.out


def test_secrets_only_ignores_sast_findings(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    secret = make_finding(
        check="secret",
        path="src/xagent/app.py",
        line=1,
        detector="detect-secrets",
        severity="error",
        identity="Secret Keyword:abc",
        detail="Secret Keyword at src/xagent/app.py:1",
    )
    sast = make_finding(
        check="sast",
        path="src/xagent/app.py",
        line=4,
        detector="semgrep",
        severity="error",
        identity="rules.no-eval",
        detail="eval is forbidden",
    )
    _write_security_baseline(clean_repo, [secret])
    monkeypatch.setattr(
        "scripts.engineering.security.collect_security_findings",
        lambda root, secrets_only=False: [secret, sast],
    )
    code = main(["--root", str(clean_repo), "security", "--secrets-only"])
    captured = capsys.readouterr()
    assert code == 0
    assert "new sast finding" not in captured.err
    assert "detect-secrets findings within frozen baseline" in captured.out


def test_security_absent_baseline_is_unknown(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    finding = make_finding(
        check="secret",
        path="src/xagent/app.py",
        line=1,
        detector="detect-secrets",
        severity="error",
        identity="Secret Keyword:abc",
        detail="Secret Keyword at src/xagent/app.py:1",
    )
    monkeypatch.setattr(
        "scripts.engineering.security.collect_security_findings",
        lambda root, secrets_only=False: [finding],
    )
    code = main(["--root", str(clean_repo), "security"])
    captured = capsys.readouterr()
    assert code == 1
    assert "unknown remains unknown" in captured.err
    assert finding.fingerprint in captured.err


def test_security_malformed_baseline_is_tool_failure(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    artifact = clean_repo / ".engineering" / "baseline.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("{nope", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.engineering.security.collect_security_findings",
        lambda root, secrets_only=False: [],
    )
    code = main(["--root", str(clean_repo), "security"])
    captured = capsys.readouterr()
    assert code == 2
    assert "malformed" in captured.err


def test_security_malformed_constraints_is_tool_failure(
    clean_repo: Path, capsys
) -> None:
    (clean_repo / "constraints.yaml").write_text("[]\n", encoding="utf-8")
    code = main(["--root", str(clean_repo), "security"])
    captured = capsys.readouterr()
    assert code == 2
    assert "must be a mapping" in captured.err


def test_collect_security_findings_secrets_only_skips_semgrep(
    clean_repo: Path, monkeypatch
) -> None:
    secret = make_finding(
        check="secret",
        path="src/xagent/app.py",
        line=1,
        detector="detect-secrets",
        severity="error",
        identity="Secret Keyword:abc",
        detail="Secret Keyword at src/xagent/app.py:1",
    )
    called = {"semgrep": False}

    monkeypatch.setattr(
        "scripts.engineering.security.collect_detect_secrets",
        lambda root, constraints: [secret],
    )

    def fake_semgrep(root, constraints):
        called["semgrep"] = True
        return []

    monkeypatch.setattr("scripts.engineering.security.collect_semgrep", fake_semgrep)
    findings = collect_security_findings(clean_repo, secrets_only=True)
    assert findings == [secret]
    assert called["semgrep"] is False
    collect_security_findings(clean_repo, secrets_only=False)
    assert called["semgrep"] is True
