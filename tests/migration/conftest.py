"""Migration fixtures must not import the developer's personal skill library."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_migration_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
