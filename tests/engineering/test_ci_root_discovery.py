"""Root-level regressions must not fall between path-scoped CI shards."""

from pathlib import Path

import yaml


def test_fast_shards_discover_every_root_test():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    selected = set()
    for name in ("pytest-fast", "pytest-fast-deepdoc"):
        for shard in workflow["jobs"][name]["strategy"]["matrix"]["include"]:
            for pattern in shard["paths"].split():
                selected.update(path.resolve() for path in root.glob(pattern))
    root_tests = set((root / "tests").glob("test_*.py"))
    assert root_tests <= selected, sorted(str(path) for path in root_tests - selected)
