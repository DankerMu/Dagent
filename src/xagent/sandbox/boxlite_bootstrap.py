"""Prepare and verify BoxLite 0.7.5's registry bootstrap cache without network I/O.

The SDK derives extracted layers and disks locally. Only the canonical Debian
image's verified OCI manifest, config and compressed layer blobs are seeded.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BOOTSTRAP_REFERENCE = "docker.io/library/debian:bookworm-slim"
SDK_VERSION = "0.7.5"
SCHEMA_VERSION = 7
_INDEX = "application/vnd.oci.image.index.v1+json"
_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
_CONFIG = "application/vnd.oci.image.config.v1+json"
_LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class BootstrapError(RuntimeError):
    """A missing or incompatible offline bootstrap prerequisite."""


@dataclass(frozen=True)
class _Image:
    manifest_digest: str
    config_digest: str
    layers: tuple[str, ...]
    blobs: tuple[tuple[Path, Path], ...]  # source -> SDK-relative destination


def _fail(reason: str, home: Path | None = None) -> BootstrapError:
    detail = f" at {home}" if home is not None else ""
    return BootstrapError(
        f"BoxLite offline bootstrap{detail}: {reason}. Prepare this worker home "
        "with python -m xagent.sandbox.boxlite_bootstrap "
        "--bootstrap-oci /path/to/bootstrap-oci --home /short/worker-home "
        "using boxlite==0.7.5; no registry fallback is permitted"
    )


def _check_sdk() -> None:
    try:
        installed = importlib.metadata.version("boxlite")
    except importlib.metadata.PackageNotFoundError as exc:
        raise _fail("boxlite==0.7.5 is not installed") from exc
    if installed != SDK_VERSION:
        raise _fail(f"cache requires boxlite=={SDK_VERSION}, installed {installed}")


def _architecture() -> str:
    machine = platform.machine().lower()
    try:
        return {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}[machine]
    except KeyError as exc:
        raise _fail(f"unsupported host architecture {machine}") from exc


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise _fail(f"unsupported OCI digest {value!r} (expected sha256)")
    return value


def _hash_file(path: Path, digest: str, size: int | None = None) -> None:
    if path.is_symlink() or not path.is_file():
        raise _fail(f"missing or unsafe blob {path}")
    hasher = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
            count += len(chunk)
    if hasher.hexdigest() != _digest(digest)[7:] or (
        size is not None and size != count
    ):
        raise _fail(f"SHA256 or descriptor size mismatch for {path}")


def _json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise _fail(f"missing or unsafe JSON {path}")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise _fail(f"invalid JSON {path}") from exc
    if not isinstance(value, dict):
        raise _fail(f"expected JSON object in {path}")
    return value


def _schema(document: dict[str, Any], media: str) -> None:
    if document.get("schemaVersion") != 2 or document.get("mediaType", media) != media:
        raise _fail(f"unsupported OCI schema or media type (expected {media})")


def _descriptor(value: Any, media: str) -> tuple[str, int]:
    if not isinstance(value, dict) or value.get("mediaType") != media:
        raise _fail(f"expected OCI {media} descriptor")
    digest = _digest(value.get("digest"))
    size = value.get("size")
    if type(size) is not int or size < 0:
        raise _fail(f"missing OCI descriptor size for {digest}")
    return digest, size


def _select(entries: Any, architecture: str) -> dict[str, Any]:
    if not isinstance(entries, list) or not entries:
        raise _fail("OCI index has no manifest descriptors")
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("platform"), dict)
        and entry["platform"].get("architecture") == architecture
        and entry["platform"].get("os") == "linux"
    ]
    # Docker's outer index may have a single platform-neutral nested index.
    if len(matches) == 1:
        return matches[0]
    if (
        not matches
        and len(entries) == 1
        and isinstance(entries[0], dict)
        and not entries[0].get("platform")
    ):
        return entries[0]
    raise _fail(f"OCI index has no unique linux/{architecture} image")


def _cache_path(digest: str, kind: str) -> Path:
    suffix = ".tar.gz" if kind == "layers" else ".json"
    return Path("images") / kind / (digest.replace(":", "-") + suffix)


def _source_image(oci: Path) -> _Image:
    if (
        oci.is_symlink()
        or not oci.is_dir()
        or _json(oci / "oci-layout").get("imageLayoutVersion") != "1.0.0"
    ):
        raise _fail(f"expected OCI layout version 1.0.0 at {oci}")
    architecture = _architecture()
    node = _json(oci / "index.json")
    _schema(node, _INDEX)
    selected: list[tuple[Path, Path]] = []
    for _ in range(8):
        descriptor = _select(node.get("manifests"), architecture)
        media = descriptor.get("mediaType")
        if media not in (_INDEX, _MANIFEST):
            raise _fail(f"unsupported OCI manifest media type {media!r}")
        digest, size = _descriptor(descriptor, media)
        source = oci / "blobs" / "sha256" / digest[7:]
        _hash_file(source, digest, size)
        node = _json(source)
        _schema(node, media)
        if media == _MANIFEST:
            selected.append((source, _cache_path(digest, "manifests")))
            break
    else:
        raise _fail("OCI index nesting exceeds eight levels")
    config_digest, config_size = _descriptor(node.get("config"), _CONFIG)
    config_source = oci / "blobs" / "sha256" / config_digest[7:]
    _hash_file(config_source, config_digest, config_size)
    config = _json(config_source)
    if config.get("architecture") != architecture or config.get("os") != "linux":
        raise _fail(f"bootstrap config does not match linux/{architecture}")
    selected.append((config_source, _cache_path(config_digest, "configs")))
    layer_descriptors = node.get("layers")
    if not isinstance(layer_descriptors, list) or not layer_descriptors:
        raise _fail("bootstrap manifest has no layers")
    layers = []
    for entry in layer_descriptors:
        layer_digest, layer_size = _descriptor(entry, _LAYER)
        layer_source = oci / "blobs" / "sha256" / layer_digest[7:]
        _hash_file(layer_source, layer_digest, layer_size)
        layers.append(layer_digest)
        selected.append((layer_source, _cache_path(layer_digest, "layers")))
    return _Image(digest, config_digest, tuple(layers), tuple(selected))


def _verified_index_row(home: Path) -> tuple[str, str, Any]:
    """Validate the SDK schema and locate its completed bootstrap image."""
    db_path = home / "db" / "boxlite.db"
    if (
        home.is_symlink()
        or (home / "db").is_symlink()
        or (home / "images").is_symlink()
        or db_path.is_symlink()
        or not db_path.is_file()
    ):
        raise _fail("SDK cache database is missing or unsafe", home)
    try:
        with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as db:
            version = db.execute(
                "SELECT version FROM schema_version WHERE id=1"
            ).fetchone()
            if version != (SCHEMA_VERSION,):
                raise _fail(
                    f"SDK cache schema must be {SCHEMA_VERSION}, found {version}", home
                )
            row = db.execute(
                "SELECT manifest_digest,config_digest,layers,complete FROM image_index WHERE reference=?",
                (BOOTSTRAP_REFERENCE,),
            ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise _fail(f"invalid SDK cache database ({exc})", home) from exc
    if row is None or row[3] != 1:
        raise _fail(
            f"complete=1 image_index row for {BOOTSTRAP_REFERENCE} is missing", home
        )
    manifest_digest, config_digest, raw_layers, _ = row
    return _digest(manifest_digest), _digest(config_digest), raw_layers


def _verify_cached_manifest(
    home: Path, manifest_digest: str, config_digest: str
) -> dict[str, Any]:
    manifest_path = home / _cache_path(manifest_digest, "manifests")
    _hash_file(manifest_path, manifest_digest)
    manifest = _json(manifest_path)
    _schema(manifest, _MANIFEST)
    if _descriptor(manifest.get("config"), _CONFIG)[0] != config_digest:
        raise _fail("image_index config digest does not match manifest", home)
    config_path = home / _cache_path(config_digest, "configs")
    _hash_file(config_path, config_digest, manifest["config"]["size"])
    config = _json(config_path)
    if config.get("os") != "linux" or config.get("architecture") != _architecture():
        raise _fail("cached bootstrap config platform does not match host", home)
    return manifest


def _verify_cached_layers(
    home: Path, manifest: dict[str, Any], raw_layers: Any
) -> None:
    try:
        layers = json.loads(raw_layers)
    except (TypeError, ValueError) as exc:
        raise _fail("image_index layers are invalid", home) from exc
    descriptors = manifest.get("layers")
    if (
        not isinstance(descriptors, list)
        or not descriptors
        or not isinstance(layers, list)
    ):
        raise _fail("image_index or manifest layers are missing", home)
    if layers != [_descriptor(entry, _LAYER)[0] for entry in descriptors]:
        raise _fail("image_index layer digests do not match manifest", home)
    for digest, entry in zip(layers, descriptors):
        _hash_file(home / _cache_path(digest, "layers"), digest, entry["size"])


def verify_bootstrap(home: Path) -> None:
    """Read-only gate before any SDK runtime construction or SimpleBox.start."""
    _check_sdk()
    home = Path(home).expanduser().absolute()
    manifest_digest, config_digest, raw_layers = _verified_index_row(home)
    manifest = _verify_cached_manifest(home, manifest_digest, config_digest)
    _verify_cached_layers(home, manifest, raw_layers)


def _require_inactive(home: Path) -> None:
    """Refuse homes held by a live SDK process or containing guest state."""
    lock_file = home / ".lock"
    if lock_file.is_symlink() or not lock_file.is_file():
        raise _fail("existing SDK home has no safe .lock file", home)
    with lock_file.open("rb") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise _fail(
                "existing SDK home is locked by an active runtime", home
            ) from exc
        fcntl.flock(stream, fcntl.LOCK_UN)


def prepare_bootstrap(oci: Path, home: Path) -> None:
    """Seed a pristine SDK home from a separately transferred OCI layout.

    Existing nonempty homes are never initialized or modified. A valid prepared
    home is accepted only if its manifest matches the supplied OCI image.
    """
    _check_sdk()
    image = _source_image(Path(oci).expanduser().absolute())
    home = Path(home).expanduser().absolute()
    if home.is_symlink() or (home.exists() and not home.is_dir()):
        raise _fail("home must be a real directory, not a link or file", home)
    if home.exists() and any(home.iterdir()):
        verify_bootstrap(home)
        _require_inactive(home)
        with sqlite3.connect(
            (home / "db" / "boxlite.db").as_uri() + "?mode=ro", uri=True
        ) as db:
            row = db.execute(
                "SELECT manifest_digest FROM image_index WHERE reference=?",
                (BOOTSTRAP_REFERENCE,),
            ).fetchone()
            boxes = db.execute("SELECT count(*) FROM box_config").fetchone()[0]
        if row != (image.manifest_digest,) or boxes:
            raise _fail(
                "existing home is active, contains boxes, or has a different bootstrap",
                home,
            )
        return
    # Construct the SDK's own schema instead of copying anyone's BoxLite DB.
    import boxlite  # type: ignore[import-untyped]

    home.mkdir(parents=True, exist_ok=True)
    runtime = boxlite.Boxlite(boxlite.Options(home_dir=str(home)))
    runtime.close()
    with sqlite3.connect(home / "db" / "boxlite.db") as db:
        version = db.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
    if version != (SCHEMA_VERSION,):
        raise _fail(
            f"SDK initialized schema {version}, expected {SCHEMA_VERSION}", home
        )
    stage = Path(tempfile.mkdtemp(prefix=".bootstrap-", dir=home))
    try:
        for source, relative in image.blobs:
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            _hash_file(target, "sha256:" + source.name, source.stat().st_size)
        for _, relative in image.blobs:
            target = home / relative
            if target.exists() or target.is_symlink():
                raise _fail(
                    f"refusing to overwrite existing SDK cache blob {target}", home
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage / relative, target)
        with sqlite3.connect(home / "db" / "boxlite.db") as db:
            db.execute(
                "INSERT INTO image_index(reference,manifest_digest,config_digest,layers,cached_at,complete) "
                "VALUES (?,?,?,?,?,1)",
                (
                    BOOTSTRAP_REFERENCE,
                    image.manifest_digest,
                    image.config_digest,
                    json.dumps(image.layers),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
    finally:
        shutil.rmtree(stage)
    verify_bootstrap(home)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Seed the pinned BoxLite offline bootstrap cache"
    )
    parser.add_argument(
        "--bootstrap-oci",
        type=Path,
        required=True,
        help="transferred Debian OCI layout",
    )
    parser.add_argument(
        "--home",
        type=Path,
        required=True,
        help="exact worker BoxLite home (short path)",
    )
    args = parser.parse_args(argv)
    try:
        prepare_bootstrap(args.bootstrap_oci, args.home)
    except (BootstrapError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
    print(f"Prepared BoxLite bootstrap at {args.home}")


if __name__ == "__main__":
    main()
