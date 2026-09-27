"""Bounded validation and extraction of locally authored skill bundles."""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path

from fastapi import HTTPException

MAX_BUNDLE_BYTES = 50 * 1024 * 1024
MAX_SKILL_MD_CHARS = 200_000
MAX_ARCHIVE_ENTRIES = 2000
MAX_FILE_PATH_CHARS = 500
_CHUNK_BYTES = 1024 * 1024
_ZERO_WIDTH = "\ufeff\u200b\u200c\u200d\u2060"
_DECODED_FILES = ("SKILL.md", "template.md")
_IGNORED_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
logger = logging.getLogger(__name__)


def validate_skill_name(name: str) -> None:
    if not name or len(name) > 64 or not NAME_RE.fullmatch(name):
        raise HTTPException(
            status_code=400,
            detail="Skill name must match [A-Za-z0-9_-]+ and be at most 64 characters.",
        )


def _canonical_path(path: str) -> str:
    return "/".join(
        segment
        for segment in path.replace("\\", "/").split("/")
        if segment and segment != "."
    )


def _is_cruft(path: str) -> bool:
    return path.startswith("__MACOSX/") or any(
        segment in _IGNORED_NAMES or segment.startswith("._")
        for segment in path.split("/")
    )


def _assert_bundle_parses(files: dict[str, bytes]) -> None:
    from xagent.skills.parser import SkillParser

    for name in _DECODED_FILES:
        content = files.get(name)
        if content is not None and len(content) > 4 * MAX_SKILL_MD_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"{name} is over {MAX_SKILL_MD_CHARS} characters.",
            )
    try:
        SkillParser.parse_bundle(name="<bundle under validation>", files=files)
    except RecursionError as exc:
        raise HTTPException(
            status_code=400,
            detail="SKILL.md frontmatter is nested too deeply to parse.",
        ) from exc
    except UnicodeDecodeError as exc:
        culprit = next(
            (
                name
                for name in _DECODED_FILES
                if name in files and not _is_utf8(files[name])
            ),
            None,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"{culprit} must be UTF-8 text."
                if culprit
                else "SKILL.md and template.md must be UTF-8 text."
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Skill bundle could not be parsed: {exc}"
        ) from exc


def _is_utf8(content: bytes) -> bool:
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _normalized_file_path(raw_path: str) -> str | None:
    """Check a file's canonical destination before it can be published."""
    path = _canonical_path(raw_path)
    if not path:
        raise HTTPException(
            status_code=400,
            detail=f"Skill bundle contains an empty file path ({raw_path!r}).",
        )
    if ".." in path.split("/"):
        raise HTTPException(
            status_code=400,
            detail="Skill file path contains a path-traversal sequence.",
        )
    if _is_cruft(path):
        return None
    hidden = next(
        (segment for segment in path.split("/") if segment.startswith(".")), None
    )
    if hidden is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Skill bundle contains a hidden file ({hidden}). Remove it and try again.",
        )
    if len(path) > MAX_FILE_PATH_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Skill file path is longer than {MAX_FILE_PATH_CHARS} characters: {path[:60]}…",
        )
    return path


def _validate_skill_document(files: dict[str, bytes]) -> None:
    """Require a substantive, parseable SKILL.md without rejecting metadata-only skills."""
    if "SKILL.md" not in files:
        raise HTTPException(status_code=400, detail="Skill has no SKILL.md.")
    md = files["SKILL.md"]
    if len(md) > 4 * MAX_SKILL_MD_CHARS:
        raise HTTPException(
            status_code=400, detail=f"SKILL.md is over {MAX_SKILL_MD_CHARS} characters."
        )
    text = md.decode("utf-8", errors="replace")
    if all(char.isspace() or char in _ZERO_WIDTH for char in text):
        raise HTTPException(status_code=400, detail="SKILL.md is empty.")
    if len(text) > MAX_SKILL_MD_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"SKILL.md is {len(text)} characters; the limit is {MAX_SKILL_MD_CHARS}.",
        )
    _assert_bundle_parses(files)


def normalize_skill_files(
    files: dict[str, bytes], *, max_bytes: int = MAX_BUNDLE_BYTES
) -> dict[str, bytes]:
    """Validate the exact canonical bytes that the writer will publish."""
    budget = min(max_bytes, MAX_BUNDLE_BYTES)
    normalized: dict[str, bytes] = {}
    total = 0
    for raw_path, content in files.items():
        path = _normalized_file_path(raw_path)
        if path is None:
            continue
        if path in normalized:
            raise HTTPException(
                status_code=400,
                detail=f"Skill bundle contains two entries for {path!r}. Remove the duplicate and try again.",
            )
        total += len(content)
        if total > budget:
            raise HTTPException(
                status_code=413, detail="Skill files exceed size budget."
            )
        normalized[path] = bytes(content)
    _validate_skill_document(normalized)
    return normalized


def _zip_member_path(entry: zipfile.ZipInfo, raw_files: dict[str, bytes]) -> str:
    """Reject unsafe or colliding archive members before reading their data."""
    path = _canonical_path(entry.filename)
    if not path or ".." in path.split("/"):
        raise HTTPException(status_code=400, detail="Skill ZIP contains unsafe paths.")
    if path in raw_files:
        raise HTTPException(
            status_code=400,
            detail=f"Skill archive contains two entries for {path!r}. Remove the duplicate and try again.",
        )
    return path


def _read_zip_member(
    archive: zipfile.ZipFile, entry: zipfile.ZipInfo, remaining: int
) -> bytes:
    """Bound both the advertised and actual inflated size of a member."""
    if entry.file_size > remaining:
        raise HTTPException(status_code=413, detail="Skill ZIP exceeds size budget.")
    chunks: list[bytes] = []
    read_total = 0
    with archive.open(entry) as member:
        while chunk := member.read(_CHUNK_BYTES):
            read_total += len(chunk)
            if read_total > remaining:
                raise HTTPException(
                    status_code=413, detail="Skill ZIP exceeds size budget."
                )
            chunks.append(chunk)
    return b"".join(chunks)


def _skill_archive_root(raw_files: dict[str, bytes]) -> str:
    """Select the single shallowest skill, rejecting ambiguous archive roots."""
    roots = [
        path
        for path in raw_files
        if (path == "SKILL.md" or path.endswith("/SKILL.md")) and not _is_cruft(path)
    ]
    if not roots:
        raise HTTPException(
            status_code=400, detail="Skill archive has no SKILL.md anywhere in it."
        )
    depth = min(path.count("/") for path in roots)
    shallowest = sorted(path for path in roots if path.count("/") == depth)
    if len(shallowest) > 1:
        examples = ", ".join(
            (path.removesuffix("SKILL.md").rstrip("/") or ".")[:60]
            for path in shallowest[:5]
        )
        if len(shallowest) > 5:
            examples += f", and {len(shallowest) - 5} more"
        raise HTTPException(
            status_code=400,
            detail=f"Skill archive contains multiple skills ({examples}). Upload one skill per archive.",
        )
    return shallowest[0].removesuffix("SKILL.md").rstrip("/")


def extract_skill_zip(
    zip_bytes: bytes, *, max_bytes: int = MAX_BUNDLE_BYTES
) -> tuple[dict[str, bytes], str]:
    """Inflate one safe ZIP bundle, returning normalized files and root name."""
    budget = min(max_bytes, MAX_BUNDLE_BYTES)
    total = 0
    raw_files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Skill archive holds {len(entries)} entries; the limit is {MAX_ARCHIVE_ENTRIES}.",
                )
            for entry in entries:
                if entry.is_dir():
                    continue
                path = _zip_member_path(entry, raw_files)
                content = _read_zip_member(archive, entry, budget - total)
                total += len(content)
                raw_files[path] = content
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Unreadable local skill archive", exc_info=True)
        raise HTTPException(
            status_code=400, detail="Skill archive is not a readable ZIP."
        ) from exc

    root = _skill_archive_root(raw_files)
    prefix = f"{root}/" if root else ""
    files = {
        path[len(prefix) :]: content
        for path, content in raw_files.items()
        if path.startswith(prefix) and path != prefix
    }
    return normalize_skill_files(files, max_bytes=budget), root.rsplit("/", 1)[-1]


def derive_upload_name(
    filename: str, root: str, skill_md: bytes, override: str | None = None
) -> str:
    """Explicit name, archive root, frontmatter, then filename stem."""
    from xagent.skills.parser import SkillParser

    if override:
        validate_skill_name(override)
        return override
    frontmatter = SkillParser._extract_frontmatter(
        skill_md.decode("utf-8", errors="replace")
    )
    fm_name = frontmatter.get("name")
    for raw in (root, fm_name if isinstance(fm_name, str) else "", Path(filename).stem):
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", raw.strip()).strip("-_")[:64]
        if slug:
            return slug
    raise HTTPException(
        status_code=400, detail="Could not derive a skill name from the upload."
    )
