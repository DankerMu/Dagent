"""Untrusted local ZIP uploads cannot escape or silently change their bundle."""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi import HTTPException

from xagent.web.api.local_skill_bundle import (
    MAX_ARCHIVE_ENTRIES,
    MAX_BUNDLE_BYTES,
    MAX_FILE_PATH_CHARS,
    MAX_SKILL_MD_CHARS,
    derive_upload_name,
    extract_skill_zip,
    normalize_skill_files,
)

SKILL_MD = b"# Test Skill\n\n## Description\nA test skill.\n"


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, contents in members.items():
            archive.writestr(name, contents)
    return buffer.getvalue()


@pytest.mark.parametrize("member", ["../escape", "dir/../../escape", "dir\\..\\escape"])
def test_traversal_members_are_rejected_before_root_filter(member):
    archive = _zip({"bundle/SKILL.md": SKILL_MD, member: b"private"})
    with pytest.raises(HTTPException) as error:
        extract_skill_zip(archive)
    assert error.value.status_code == 400
    assert "unsafe paths" in error.value.detail


def test_root_is_shallowest_not_alphabetical_and_companions_survive():
    files, root = extract_skill_zip(
        _zip(
            {
                "pdf-tools/SKILL.md": SKILL_MD,
                "pdf-tools/reference.md": b"reference",
                "pdf-tools/Examples/SKILL.md": b"# Example",
            }
        )
    )
    assert root == "pdf-tools"
    assert files == {
        "SKILL.md": SKILL_MD,
        "reference.md": b"reference",
        "Examples/SKILL.md": b"# Example",
    }


def test_ambiguous_roots_are_not_silently_selected():
    with pytest.raises(HTTPException) as error:
        extract_skill_zip(_zip({"a/SKILL.md": SKILL_MD, "b/SKILL.md": SKILL_MD}))
    assert error.value.status_code == 400
    assert "multiple skills" in error.value.detail


def test_duplicate_canonical_member_paths_are_rejected():
    with pytest.raises(HTTPException) as error:
        extract_skill_zip(_zip({"SKILL.md": SKILL_MD, "./SKILL.md": b"# Wrong"}))
    assert error.value.status_code == 400
    assert "two entries" in error.value.detail


def test_unreadable_archive_is_client_error():
    with pytest.raises(HTTPException) as error:
        extract_skill_zip(b"not a zip")
    assert error.value.status_code == 400


def test_entry_and_expanded_byte_budgets_are_enforced():
    directory_padding = _zip(
        {"SKILL.md": SKILL_MD} | {f"pad{i}/": b"" for i in range(MAX_ARCHIVE_ENTRIES)}
    )
    with pytest.raises(HTTPException) as entries:
        extract_skill_zip(directory_padding)
    assert entries.value.status_code == 400
    assert "entries" in entries.value.detail

    with pytest.raises(HTTPException) as size:
        extract_skill_zip(
            _zip({"SKILL.md": SKILL_MD, "big.bin": b"x" * (MAX_BUNDLE_BYTES + 1)})
        )
    assert size.value.status_code == 413


def test_unicode_content_and_paths_are_bounded_before_publication():
    assert normalize_skill_files({"SKILL.md": ("€" * MAX_SKILL_MD_CHARS).encode()})
    for files in (
        {"SKILL.md": b"x" * (MAX_SKILL_MD_CHARS + 1)},
        {"SKILL.md": SKILL_MD, "n" * (MAX_FILE_PATH_CHARS + 1): b"x"},
        {"SKILL.md": SKILL_MD, "template.md": b"\xff"},
    ):
        with pytest.raises(HTTPException) as error:
            normalize_skill_files(files)
        assert error.value.status_code == 400


def test_override_must_be_literal_valid_name():
    assert derive_upload_name("archive.zip", "bundle", SKILL_MD, "_skill_") == "_skill_"
    with pytest.raises(HTTPException) as error:
        derive_upload_name("archive.zip", "bundle", SKILL_MD, "bad name!")
    assert error.value.status_code == 400
