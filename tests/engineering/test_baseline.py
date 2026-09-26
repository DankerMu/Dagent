from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.engineering.check import main
from scripts.engineering.config import load_constraints
from scripts.engineering.findings import (
    compare_findings,
    load_baseline,
    make_finding,
    serialize_baseline,
)
from scripts.engineering.tools import ToolFailure


def _write_baseline(root: Path, findings) -> Path:
    artifact = root / ".engineering" / "baseline.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    payload = serialize_baseline(
        reference=load_constraints(root).baseline_rev,
        tool_identities={"lizard": "lizard 1"},
        findings=findings,
    )
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    return artifact


def _stub_collectors(monkeypatch, findings=None) -> None:
    empty = findings if findings is not None else []
    monkeypatch.setattr(
        "scripts.engineering.baseline.collect_all_findings", lambda root: list(empty)
    )
    monkeypatch.setattr(
        "scripts.engineering.baseline._identities",
        lambda root: {"lizard": "lizard 1.24.0"},
    )


def test_guardrails_fail_when_baseline_absent(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(
        "scripts.engineering.guardrails.collect_guardrail_findings",
        lambda root: [],
    )
    code = main(["--root", str(clean_repo), "guardrails"])
    captured = capsys.readouterr()
    assert code == 1
    assert "unknown remains unknown" in captured.err
    assert ".engineering/baseline.json" in captured.err


def test_baseline_write_without_resolvable_reference_fails(
    clean_repo: Path, capsys
) -> None:
    code = main(
        [
            "--root",
            str(clean_repo),
            "baseline",
            "--write",
            "--reference",
            "definitely-missing-ref",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert not (clean_repo / ".engineering" / "baseline.json").exists()
    assert "baseline:" in captured.err


def test_baseline_count_increase_is_rejected(clean_repo: Path) -> None:
    first = make_finding(
        check="size",
        path="src/xagent/a.py",
        line=801,
        detector="file-size",
        severity="error",
        identity="801",
        detail="physical source lines 801 exceed 800",
    )
    second = make_finding(
        check="size",
        path="src/xagent/b.py",
        line=801,
        detector="file-size",
        severity="error",
        identity="900",
        detail="physical source lines 900 exceed 800",
    )
    artifact = _write_baseline(clean_repo, [first])
    from scripts.engineering.findings import compare_findings, load_baseline

    errors = compare_findings([first, second], load_baseline(artifact))
    assert any("count rose" in item or "new size finding" in item for item in errors)


def test_baseline_rejects_swapped_issue(clean_repo: Path) -> None:
    old = make_finding(
        check="size",
        path="src/xagent/legacy.py",
        line=801,
        detector="file-size",
        severity="error",
        identity="801",
        detail="physical source lines 801 exceed 800",
    )
    new = make_finding(
        check="size",
        path="src/xagent/app.py",
        line=801,
        detector="file-size",
        severity="error",
        identity="900",
        detail="physical source lines 900 exceed 800",
    )
    _write_baseline(clean_repo, [old])
    from scripts.engineering.findings import compare_findings, load_baseline

    errors = compare_findings(
        [new], load_baseline(clean_repo / ".engineering" / "baseline.json")
    )
    assert errors
    assert any("new size finding" in item for item in errors)


def test_baseline_rejects_severity_raise(clean_repo: Path) -> None:
    frozen = make_finding(
        check="sast",
        path="src/xagent/app.py",
        line=4,
        detector="semgrep",
        severity="medium",
        identity="rule.x",
        detail="old",
    )
    raised = make_finding(
        check="sast",
        path="src/xagent/app.py",
        line=4,
        detector="semgrep",
        severity="error",
        identity="rule.x",
        detail="worse",
    )
    _write_baseline(clean_repo, [frozen])
    from scripts.engineering.findings import compare_findings, load_baseline

    errors = compare_findings(
        [raised], load_baseline(clean_repo / ".engineering" / "baseline.json")
    )
    assert any("raised severity" in item for item in errors)


def test_baseline_rejects_secret_strings_in_artifact(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    artifact = clean_repo / ".engineering" / "baseline.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "schema": "dagent.engineering.baseline.v1",
                "reference": load_constraints(clean_repo).baseline_rev,
                "findings": [
                    {
                        "fingerprint": "x",
                        "secret": "AKIA123",  # pragma: allowlist secret - rejected synthetic payload
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.engineering.guardrails.collect_guardrail_findings",
        lambda root: [],
    )
    code = main(["--root", str(clean_repo), "guardrails"])
    captured = capsys.readouterr()
    assert code == 2
    assert "must not store secret strings" in captured.err


def test_baseline_write_captures_pristine_snapshot(
    git_repo: tuple[Path, str], capsys, monkeypatch
) -> None:
    root, sha = git_repo
    seen: dict[str, Path] = {}

    def collect(snapshot: Path):
        seen["root"] = snapshot
        assert (snapshot / "src" / "xagent" / "app.py").is_file()
        assert not (snapshot / ".git").exists()
        return []

    monkeypatch.setattr("scripts.engineering.baseline.collect_all_findings", collect)
    monkeypatch.setattr(
        "scripts.engineering.baseline._identities",
        lambda root: {"lizard": "lizard 1.24.0"},
    )
    code = main(["--root", str(root), "baseline", "--write", "--reference", sha])
    captured = capsys.readouterr()
    artifact = root / ".engineering" / "baseline.json"
    assert code == 0
    assert artifact.is_file()
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["reference"] == sha
    assert payload["findings"] == []
    assert "wrote" in captured.out
    assert "initialization files were not grandfathered" in captured.out
    assert "root" in seen


def test_baseline_write_missing_tool_does_not_create_artifact(
    git_repo: tuple[Path, str], capsys, monkeypatch
) -> None:
    root, sha = git_repo
    monkeypatch.setattr(
        "scripts.engineering.baseline.collect_all_findings",
        lambda snapshot: (_ for _ in ()).throw(ToolFailure("lizard is not installed")),
    )
    code = main(["--root", str(root), "baseline", "--write", "--reference", sha])
    captured = capsys.readouterr()
    assert code == 2
    assert "lizard is not installed" in captured.err
    assert not (root / ".engineering" / "baseline.json").exists()


def test_baseline_compare_clean_match(clean_repo: Path, capsys, monkeypatch) -> None:
    finding = make_finding(
        check="size",
        path="src/xagent/app.py",
        line=801,
        detector="file-size",
        severity="error",
        identity="801",
        detail="physical source lines 801 exceed 800",
    )
    _write_baseline(clean_repo, [finding])
    _stub_collectors(monkeypatch, [finding])
    code = main(["--root", str(clean_repo), "baseline"])
    captured = capsys.readouterr()
    assert code == 0
    assert "findings match frozen" in captured.out


def test_baseline_compare_absent_artifact_is_unknown(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    _stub_collectors(monkeypatch, [])
    code = main(["--root", str(clean_repo), "baseline"])
    captured = capsys.readouterr()
    assert code == 1
    assert "unknown remains unknown" in captured.err


def test_baseline_compare_rejects_new_finding(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    frozen = make_finding(
        check="size",
        path="src/xagent/legacy.py",
        line=801,
        detector="file-size",
        severity="error",
        identity="801",
        detail="physical source lines 801 exceed 800",
    )
    current = make_finding(
        check="size",
        path="src/xagent/app.py",
        line=801,
        detector="file-size",
        severity="error",
        identity="900",
        detail="physical source lines 900 exceed 800",
    )
    _write_baseline(clean_repo, [frozen])
    _stub_collectors(monkeypatch, [current])
    code = main(["--root", str(clean_repo), "baseline"])
    captured = capsys.readouterr()
    assert code == 1
    assert "new size finding" in captured.err


def test_baseline_compare_rejects_base_mismatch(
    git_repo: tuple[Path, str], capsys, monkeypatch
) -> None:
    root, sha = git_repo
    finding = make_finding(
        check="naming",
        path="src/xagent/helper_v2.py",
        line=1,
        detector="naming",
        severity="error",
        identity="_v2",
        detail="forbidden naming suffix matching _v2",
    )
    artifact = root / ".engineering" / "baseline.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    payload = serialize_baseline(
        reference=load_constraints(root).baseline_rev,
        tool_identities={"lizard": "lizard 1"},
        findings=[finding],
    )
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    _stub_collectors(monkeypatch, [finding])
    code = main(["--root", str(root), "--base", sha, "baseline"])
    captured = capsys.readouterr()
    assert code == 1
    assert "does not match --base" in captured.err


def test_baseline_reference_without_write_is_usage_error(
    clean_repo: Path, capsys
) -> None:
    code = main(["--root", str(clean_repo), "baseline", "--reference", "abc"])
    captured = capsys.readouterr()
    assert code == 2
    assert "--reference is only valid with --write" in captured.err


def test_guardrails_accept_frozen_findings(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    finding = make_finding(
        check="naming",
        path="src/xagent/helper_v2.py",
        line=1,
        detector="naming",
        severity="error",
        identity="_v2",
        detail="forbidden naming suffix matching _v2",
    )
    _write_baseline(clean_repo, [finding])
    monkeypatch.setattr(
        "scripts.engineering.guardrails.collect_guardrail_findings",
        lambda root: [finding],
    )
    code = main(["--root", str(clean_repo), "guardrails"])
    captured = capsys.readouterr()
    assert code == 0
    assert "within frozen baseline, all conform" in captured.out


def test_guardrails_malformed_baseline_is_tool_failure(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    artifact = clean_repo / ".engineering" / "baseline.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("{nope", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.engineering.guardrails.collect_guardrail_findings",
        lambda root: [],
    )
    code = main(["--root", str(clean_repo), "guardrails"])
    captured = capsys.readouterr()
    assert code == 2
    assert "malformed" in captured.err


@pytest.mark.parametrize("schema", [None, "dagent.engineering.baseline.v0"])
def test_guardrails_rejects_invalid_baseline_schema(
    clean_repo: Path, capsys, monkeypatch, schema: str | None
) -> None:
    artifact = _write_baseline(clean_repo, [])
    payload = json.loads(artifact.read_text())
    if schema is None:
        payload.pop("schema")
    else:
        payload["schema"] = schema
    artifact.write_text(json.dumps(payload))
    monkeypatch.setattr(
        "scripts.engineering.guardrails.collect_guardrail_findings",
        lambda root: [],
    )
    code = main(["--root", str(clean_repo), "guardrails"])
    assert code == 2
    assert "baseline schema must be" in capsys.readouterr().err


def test_guardrails_missing_tool_is_failure(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(
        "scripts.engineering.guardrails.collect_guardrail_findings",
        lambda root: (_ for _ in ()).throw(ToolFailure("vulture is not installed")),
    )
    code = main(["--root", str(clean_repo), "guardrails"])
    captured = capsys.readouterr()
    assert code == 2
    assert "tool failure" in captured.err
    assert "vulture is not installed" in captured.err


def test_guardrails_malformed_constraints_is_tool_failure(
    clean_repo: Path, capsys
) -> None:
    (clean_repo / "constraints.yaml").write_text("[]\n", encoding="utf-8")
    code = main(["--root", str(clean_repo), "guardrails"])
    captured = capsys.readouterr()
    assert code == 2
    assert "must be a mapping" in captured.err


def test_load_baseline_rejects_non_object_and_missing_fingerprint(
    tmp_path: Path,
) -> None:
    path = tmp_path / "baseline.json"
    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        load_baseline(path)
    path.write_text(json.dumps({"findings": ["nope"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing fingerprint"):
        load_baseline(path)


def test_compare_findings_rejects_non_integer_count(clean_repo: Path) -> None:
    artifact = _write_baseline(clean_repo, [])
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["counts"] = {"size": "two"}
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    errors = compare_findings([], load_baseline(artifact))
    assert any("not an integer" in item for item in errors)


def test_baseline_identities_require_installed_tools(
    clean_repo: Path, monkeypatch
) -> None:
    from scripts.engineering.baseline import _identities

    monkeypatch.setattr(
        "scripts.engineering.baseline.require_executable",
        lambda name: (_ for _ in ()).throw(ToolFailure(f"{name} is not installed")),
    )
    with pytest.raises(ToolFailure, match="is not installed"):
        _identities(clean_repo)


def test_baseline_compare_malformed_constraints_is_tool_failure(
    clean_repo: Path, capsys
) -> None:
    (clean_repo / "constraints.yaml").write_text("[]\n", encoding="utf-8")
    code = main(["--root", str(clean_repo), "baseline"])
    captured = capsys.readouterr()
    assert code == 2
    assert "must be a mapping" in captured.err


def test_baseline_write_copies_constraints_into_snapshot(
    git_repo: tuple[Path, str], capsys, monkeypatch
) -> None:
    root, sha = git_repo
    seen: dict[str, bool] = {}
    historical_config_finding = make_finding(
        check="secret",
        path="constraints.yaml",
        line=1,
        detector="fixture",
        severity="error",
        identity="historical-config",
        detail="historical detector config finding",
    )

    def collect(snapshot):
        seen["constraints"] = (snapshot / "constraints.yaml").is_file()
        seen["semgrep"] = (snapshot / ".semgrep.yml").is_file()
        return [historical_config_finding]

    monkeypatch.setattr("scripts.engineering.baseline.collect_all_findings", collect)
    monkeypatch.setattr(
        "scripts.engineering.baseline._identities",
        lambda root: {"lizard": "lizard 1.24.0"},
    )
    code = main(["--root", str(root), "baseline", "--write", "--reference", sha])
    captured = capsys.readouterr()
    assert code == 0
    assert seen["constraints"] is True
    assert seen["semgrep"] is True
    assert "wrote" in captured.out
    payload = json.loads((root / ".engineering" / "baseline.json").read_text())
    assert [finding["path"] for finding in payload["findings"]] == ["constraints.yaml"]


def test_baseline_write_missing_frontend_bins_is_tool_failure(
    git_repo: tuple[Path, str], capsys, monkeypatch
) -> None:
    root, sha = git_repo
    monkeypatch.setattr(
        "scripts.engineering.baseline.collect_all_findings", lambda snapshot: []
    )
    monkeypatch.setattr(
        "scripts.engineering.baseline._identities",
        lambda root: (_ for _ in ()).throw(
            ToolFailure("jscpd is not installed at frontend/node_modules/.bin/jscpd")
        ),
    )
    code = main(["--root", str(root), "baseline", "--write", "--reference", sha])
    captured = capsys.readouterr()
    assert code == 2
    assert "jscpd is not installed" in captured.err
    assert not (root / ".engineering" / "baseline.json").exists()


def test_guardrails_reject_new_finding_against_frozen_baseline(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    frozen = make_finding(
        check="naming",
        path="src/xagent/helper_v2.py",
        line=1,
        detector="naming",
        severity="error",
        identity="_v2",
        detail="forbidden naming suffix matching _v2",
    )
    current = make_finding(
        check="naming",
        path="src/xagent/helper_v3.py",
        line=1,
        detector="naming",
        severity="error",
        identity="_v3",
        detail="forbidden naming suffix matching _v3",
    )
    _write_baseline(clean_repo, [frozen])
    monkeypatch.setattr(
        "scripts.engineering.guardrails.collect_guardrail_findings",
        lambda root: [current],
    )
    code = main(["--root", str(clean_repo), "guardrails"])
    captured = capsys.readouterr()
    assert code == 1
    assert "new naming finding" in captured.err


def test_baseline_compare_unresolvable_base_is_tool_failure(
    git_repo: tuple[Path, str], capsys, monkeypatch
) -> None:
    root, _sha = git_repo
    finding = make_finding(
        check="naming",
        path="src/xagent/helper_v2.py",
        line=1,
        detector="naming",
        severity="error",
        identity="_v2",
        detail="forbidden naming suffix matching _v2",
    )
    _write_baseline(root, [finding])
    _stub_collectors(monkeypatch, [finding])
    code = main(["--root", str(root), "--base", "missing-ref", "baseline"])
    captured = capsys.readouterr()
    assert code == 2
    assert "baseline:" in captured.err


def test_load_baseline_missing_file_and_non_list_findings(tmp_path: Path) -> None:
    missing = tmp_path / "absent.json"
    with pytest.raises(FileNotFoundError):
        load_baseline(missing)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"findings": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="findings must be a list"):
        load_baseline(path)


def test_collect_guardrail_findings_aggregates_canonical_and_detectors(
    clean_repo: Path, monkeypatch
) -> None:
    from scripts.engineering.guardrails import collect_guardrail_findings

    size = make_finding(
        check="size",
        path="src/xagent/app.py",
        line=801,
        detector="file-size",
        severity="error",
        identity="801",
        detail="physical source lines 801 exceed 800",
    )
    monkeypatch.setattr(
        "scripts.engineering.guardrails.collect_size_findings", lambda root, c: [size]
    )
    monkeypatch.setattr(
        "scripts.engineering.guardrails.collect_naming_findings", lambda root, c: []
    )
    monkeypatch.setattr(
        "scripts.engineering.guardrails.collect_scratch_findings", lambda root, c: []
    )
    for name in ("collect_lizard", "collect_vulture", "collect_knip", "collect_jscpd"):
        monkeypatch.setattr(
            f"scripts.engineering.guardrails.{name}", lambda root, c: []
        )
    findings = collect_guardrail_findings(clean_repo)
    assert findings == [size]


def test_baseline_write_symlinks_frontend_modules(
    git_repo: tuple[Path, str], capsys, monkeypatch
) -> None:
    root, sha = git_repo
    modules = root / "frontend" / "node_modules" / ".bin"
    modules.mkdir(parents=True, exist_ok=True)
    (modules / "jscpd").write_text("#!/bin/sh\n", encoding="utf-8")
    seen: dict[str, bool] = {}

    def collect(snapshot):
        dest = snapshot / "frontend" / "node_modules"
        seen["linked"] = dest.is_symlink()
        return []

    monkeypatch.setattr("scripts.engineering.baseline.collect_all_findings", collect)
    monkeypatch.setattr(
        "scripts.engineering.baseline._identities",
        lambda root: {"lizard": "lizard 1.24.0"},
    )
    code = main(["--root", str(root), "baseline", "--write", "--reference", sha])
    captured = capsys.readouterr()
    assert code == 0
    assert seen["linked"] is True
    assert "wrote" in captured.out


def test_baseline_compare_collect_failure_is_tool_failure(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    finding = make_finding(
        check="size",
        path="src/xagent/app.py",
        line=801,
        detector="file-size",
        severity="error",
        identity="801",
        detail="physical source lines 801 exceed 800",
    )
    _write_baseline(clean_repo, [finding])
    monkeypatch.setattr(
        "scripts.engineering.baseline.collect_all_findings",
        lambda root: (_ for _ in ()).throw(ToolFailure("semgrep is not installed")),
    )
    code = main(["--root", str(clean_repo), "baseline"])
    captured = capsys.readouterr()
    assert code == 2
    assert "semgrep is not installed" in captured.err
