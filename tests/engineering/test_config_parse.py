from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.engineering.config import (
    ConfigError,
    is_exempt,
    iter_files,
    iter_source_files,
    load_constraints,
    relative_to_root,
    require_dict,
    require_int,
    require_list,
    require_str,
)
from tests.engineering.conftest import write_constraints


def test_constraints_load_accepts_schema(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    assert constraints.schema_version == "3.6.0"
    assert constraints.max_file_lines == 800
    assert constraints.max_complexity == 15
    assert constraints.max_function_lines == 100
    assert constraints.duplicate_percent == 3
    assert constraints.min_line_coverage == 80
    assert constraints.ratchet_owner == "DankerMu"
    assert constraints.baseline_rev == constraints.raw["baseline"]["rev"]


def test_constraints_reject_non_integer_threshold(clean_repo: Path) -> None:
    write_constraints(
        clean_repo, **{"size_limits.max_file_lines.value": "eight hundred"}
    )
    with pytest.raises(ConfigError, match="must be an integer"):
        load_constraints(clean_repo)


def test_docs_rejects_malformed_constraints(clean_repo: Path, capsys) -> None:
    from scripts.engineering.check import main

    (clean_repo / "constraints.yaml").write_text("[]\n", encoding="utf-8")
    code = main(["--root", str(clean_repo), "docs"])
    captured = capsys.readouterr()
    assert code == 2
    assert "must be a mapping" in captured.err


def test_iter_files_inventories_nested_app_build_not_generated_out(
    clean_repo: Path,
) -> None:
    app_build = clean_repo / "frontend" / "src" / "app" / "build" / "page.tsx"
    app_build.parent.mkdir(parents=True, exist_ok=True)
    app_build.write_text("export default function Page() { return null; }\n")
    generated = clean_repo / "frontend" / "out" / "index.html"
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_text("<html></html>\n")
    root_build = clean_repo / "build" / "artifact.js"
    root_build.parent.mkdir(parents=True, exist_ok=True)
    root_build.write_text("export const generated = true;\n")
    rels = {relative_to_root(clean_repo, path) for path in iter_files(clean_repo)}
    assert "frontend/src/app/build/page.tsx" in rels
    assert "frontend/out/index.html" not in rels
    assert "build/artifact.js" not in rels
    sources = {
        relative_to_root(clean_repo, path) for path in iter_source_files(clean_repo)
    }
    assert "frontend/src/app/build/page.tsx" in sources
    assert "src/xagent/app.py" in sources


def test_iter_files_does_not_follow_symlinked_directories(
    clean_repo: Path,
) -> None:
    outside = clean_repo.parent / f"{clean_repo.name}-outside"
    outside.mkdir()
    secret = outside / "leaked.py"
    secret.write_text("SECRET = 1\n", encoding="utf-8")
    link = clean_repo / "linked-src"
    link.symlink_to(outside, target_is_directory=True)
    rels = {relative_to_root(clean_repo, path) for path in iter_files(clean_repo)}
    assert "linked-src/leaked.py" not in rels
    assert all("leaked.py" not in rel for rel in rels)
    assert all(not rel.startswith("linked-src/") for rel in rels)


def test_load_constraints_missing_file_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="constraints.yaml is missing"):
        load_constraints(tmp_path)


def test_require_helpers_reject_wrong_types() -> None:
    with pytest.raises(ConfigError, match="missing required key"):
        require_int({}, "size_limits.max_file_lines.value")
    with pytest.raises(ConfigError, match="must be an integer"):
        require_int({"n": True}, "n")
    with pytest.raises(ConfigError, match="must be a non-empty string"):
        require_str({"name": "  "}, "name")
    with pytest.raises(ConfigError, match="must be a list"):
        require_list({"items": {}}, "items")
    with pytest.raises(ConfigError, match="must be a mapping"):
        require_dict({"body": []}, "body")


def test_is_exempt_matches_path_prefix(clean_repo: Path) -> None:
    write_constraints(
        clean_repo,
        **{
            "exemptions.entries": [
                {
                    "path": "src/xagent/legacy.py",
                    "rules": ["size_limits"],
                    "reason": "split later",
                    "exit_condition": "split files",
                }
            ]
        },
    )
    constraints = load_constraints(clean_repo)
    assert is_exempt(constraints, "src/xagent/legacy.py", "size_limits")
    assert not is_exempt(constraints, "src/xagent/app.py", "size_limits")
    assert not is_exempt(constraints, "src/xagent/legacy.py", "dead_code")


def test_load_constraints_rejects_schema_mismatch(clean_repo: Path) -> None:
    write_constraints(clean_repo, **{"strictness_profile.schema_version": "0.0.0"})
    with pytest.raises(ConfigError, match="schema_version expected"):
        load_constraints(clean_repo)


def test_load_constraints_rejects_incomplete_exemption(clean_repo: Path) -> None:
    write_constraints(
        clean_repo,
        **{
            "exemptions.entries": [
                {"path": "src/xagent/app.py", "rules": ["size_limits"]}
            ]
        },
    )
    with pytest.raises(ConfigError, match="exemption is missing reason"):
        load_constraints(clean_repo)


def test_iter_files_skips_cache_directories(clean_repo: Path) -> None:
    cache = clean_repo / "node_modules" / "pkg" / "index.js"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("module.exports = 1;\n", encoding="utf-8")
    pycache = clean_repo / "src" / "xagent" / "__pycache__" / "app.cpython-312.pyc"
    pycache.parent.mkdir(parents=True, exist_ok=True)
    pycache.write_bytes(b"\x00")
    rels = {relative_to_root(clean_repo, path) for path in iter_files(clean_repo)}
    assert "node_modules/pkg/index.js" not in rels
    assert "src/xagent/__pycache__/app.cpython-312.pyc" not in rels


def test_load_constraints_rejects_non_mapping_exemption(clean_repo: Path) -> None:
    write_constraints(clean_repo, **{"exemptions.entries": ["nope"]})
    with pytest.raises(ConfigError, match="must be mappings"):
        load_constraints(clean_repo)


def test_load_constraints_rejects_non_string_scratchpad(clean_repo: Path) -> None:
    write_constraints(
        clean_repo,
        **{"code_canonicality.scratchpad_directories.paths": [123]},
    )
    with pytest.raises(ConfigError, match="must be strings"):
        load_constraints(clean_repo)


def test_load_constraints_rejects_surface_without_command(clean_repo: Path) -> None:
    write_constraints(
        clean_repo, **{"verification.surfaces": {"docs": {"evidence": "none"}}}
    )
    with pytest.raises(ConfigError, match="command is required"):
        load_constraints(clean_repo)


def test_load_constraints_rejects_blank_surface_command(clean_repo: Path) -> None:
    write_constraints(
        clean_repo, **{"verification.surfaces": {"docs": {"command": "  "}}}
    )
    with pytest.raises(ConfigError, match="command must be a string"):
        load_constraints(clean_repo)


def test_load_constraints_rejects_generated_section_without_title(
    clean_repo: Path,
) -> None:
    write_constraints(clean_repo, **{"generated_sections.agents_md": [{}]})
    with pytest.raises(ConfigError, match="need title"):
        load_constraints(clean_repo)


@pytest.mark.parametrize("revision", [None, 42, "  "])
def test_load_constraints_requires_string_baseline_revision(
    clean_repo: Path, revision: object
) -> None:
    write_constraints(clean_repo, **{"baseline.rev": revision})
    with pytest.raises(ConfigError, match="baseline.rev must be a non-empty string"):
        load_constraints(clean_repo)


def test_load_constraints_requires_baseline_revision_key(clean_repo: Path) -> None:
    data = load_constraints(clean_repo).raw
    del data["baseline"]["rev"]
    (clean_repo / "constraints.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="missing required key baseline.rev"):
        load_constraints(clean_repo)
