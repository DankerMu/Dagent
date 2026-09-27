import os
import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_package_version

from fastapi import APIRouter

system_router = APIRouter(prefix="/api/system", tags=["system"])


def _resolve_backend_version() -> str:
    injected_version = os.getenv("XAGENT_VERSION") or ""
    if injected_version:
        return injected_version

    try:
        package_version = get_package_version("xagent")
        if package_version:
            return package_version
    except PackageNotFoundError:
        pass

    return "unknown"


def _build_display_version(version: str, commit: str) -> str:
    raw_version = version.strip()
    if not raw_version or raw_version == "unknown":
        return "unknown"

    normalized = raw_version[1:] if raw_version.startswith("v") else raw_version
    plus_index = normalized.find("+")
    without_local = normalized[:plus_index] if plus_index >= 0 else normalized
    base_version = (
        without_local.split(".dev")[0] if ".dev" in without_local else without_local
    )

    short_hash = commit.strip()
    if not short_hash and plus_index >= 0:
        local_part = normalized[plus_index + 1 :]
        hash_match = re.search(r"g([0-9a-f]{5,40})", local_part, re.IGNORECASE)
        short_hash = hash_match.group(1) if hash_match else ""

    if not base_version:
        return "unknown"

    return f"v{base_version}{f'-{short_hash[:5]}' if short_hash else ''}"


@system_router.get("/version")
async def get_version() -> dict[str, str]:
    raw_commit = os.getenv("XAGENT_GIT_COMMIT") or os.getenv("GITHUB_SHA") or ""
    commit = raw_commit[:12] if raw_commit else ""
    build_time = os.getenv("XAGENT_BUILD_TIME") or ""
    current_version = _resolve_backend_version()
    return {
        "version": current_version,
        "display_version": _build_display_version(current_version, raw_commit),
        "commit": commit,
        "build_time": build_time,
    }
