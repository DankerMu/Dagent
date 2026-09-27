"""Small, hashed OCI bootstrap fixture; no external image or network required."""

import gzip
import hashlib
import json
import platform
from pathlib import Path

import pytest

from xagent.sandbox.boxlite_bootstrap import prepare_bootstrap


@pytest.fixture
def bootstrap_oci(tmp_path: Path) -> Path:
    oci = tmp_path / "bootstrap-oci"
    (oci / "blobs" / "sha256").mkdir(parents=True)
    (oci / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64"}[
        platform.machine().lower()
    ]

    def blob(data: bytes, media: str) -> dict:
        digest = hashlib.sha256(data).hexdigest()
        (oci / "blobs" / "sha256" / digest).write_bytes(data)
        return {"mediaType": media, "digest": f"sha256:{digest}", "size": len(data)}

    config = blob(
        json.dumps({"architecture": arch, "os": "linux"}).encode(),
        "application/vnd.oci.image.config.v1+json",
    )
    layer = blob(
        gzip.compress(b"sandbox layer", mtime=0),
        "application/vnd.oci.image.layer.v1.tar+gzip",
    )
    manifest = blob(
        json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": config,
                "layers": [layer],
            }
        ).encode(),
        "application/vnd.oci.image.manifest.v1+json",
    )
    manifest["platform"] = {"os": "linux", "architecture": arch}
    nested = blob(
        json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "manifests": [manifest],
            }
        ).encode(),
        "application/vnd.oci.image.index.v1+json",
    )
    (oci / "index.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "manifests": [nested],
            }
        )
    )
    return oci


@pytest.fixture
def prepared_boxlite_home(tmp_path: Path, bootstrap_oci: Path) -> Path:
    home = tmp_path / "boxlite-home"
    prepare_bootstrap(bootstrap_oci, home)
    return home
