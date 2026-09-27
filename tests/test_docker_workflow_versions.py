from __future__ import annotations

import ast
import subprocess
import tomllib
from pathlib import Path
from typing import Any, cast

import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def test_postgresql_dependency_groups_include_async_trace_driver():
    """Default-on async writes need Psycopg 3 in image, CI and documented extras."""
    project = tomllib.loads(read_repo_file("pyproject.toml"))
    install_sets = [
        project["dependency-groups"][group] for group in ("backend-image", "test")
    ] + [
        project["project"]["optional-dependencies"][extra]
        for extra in ("postgresql", "all")
    ]
    for install_set in install_sets:
        requirements = {
            Requirement(item).name for item in install_set if isinstance(item, str)
        }
        assert {"psycopg2-binary", "psycopg"} <= requirements


def read_workflow(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text()


def read_repo_file(path: str) -> str:
    return (ROOT / path).read_text()


def pyproject_section(pyproject: str, section_name: str) -> str:
    marker = f"[{section_name}]"
    start = pyproject.index(marker)
    next_section = pyproject.find("\n[", start + len(marker))
    if next_section == -1:
        return pyproject[start:]
    return pyproject[start:next_section]


def test_nightly_build_uses_pep440_package_version() -> None:
    workflow = read_workflow("nightly-build.yml")

    assert 'NIGHTLY_VERSION="nightly-$NIGHTLY_DATE"' in workflow
    assert 'PACKAGE_VERSION="0.0.dev$NIGHTLY_DATE"' in workflow
    assert (
        "XAGENT_VERSION=${{ steps.version-meta.outputs.nightly_version }}" in workflow
    )
    assert (
        "XAGENT_PACKAGE_VERSION=${{ steps.version-meta.outputs.package_version }}"
        in workflow
    )
    assert 'python scripts/write_package_version.py "$PACKAGE_VERSION"' not in workflow
    assert 'echo "package_version=$PACKAGE_VERSION" >> "$GITHUB_OUTPUT"' in workflow
    assert (
        "XAGENT_PACKAGE_VERSION=${{ steps.version-meta.outputs.nightly_version }}"
        not in workflow
    )


def test_release_build_sanitizes_package_version_for_manual_runs() -> None:
    workflow = read_workflow("docker-publish.yml")

    assert 'PACKAGE_VERSION="${RELEASE_VERSION#v}"' in workflow
    assert 'PACKAGE_VERSION="0.0.0+${GITHUB_SHA::12}"' in workflow
    assert (
        "XAGENT_VERSION=${{ steps.version-meta.outputs.release_version }}" in workflow
    )
    assert (
        "XAGENT_PACKAGE_VERSION=${{ steps.version-meta.outputs.package_version }}"
        in workflow
    )
    assert 'python scripts/write_package_version.py "$PACKAGE_VERSION"' not in workflow
    assert 'echo "package_version=$PACKAGE_VERSION" >> "$GITHUB_OUTPUT"' in workflow
    assert (
        "XAGENT_PACKAGE_VERSION=${{ steps.version-meta.outputs.release_version }}"
        not in workflow
    )


def test_backend_dockerfile_applies_package_specific_vcs_version() -> None:
    dockerfile = read_repo_file("docker/Dockerfile.backend")

    assert dockerfile.count('ARG XAGENT_PACKAGE_VERSION="0.0.0+docker"') == 2
    assert (
        dockerfile.count('SETUPTOOLS_SCM_PRETEND_VERSION="${XAGENT_PACKAGE_VERSION}"')
        == 2
    )
    dependency_sync = (
        'SETUPTOOLS_SCM_PRETEND_VERSION="${XAGENT_PACKAGE_VERSION}" \\\n'
        "    VIRTUAL_ENV=/opt/venv uv sync --active --locked --no-dev "
        "--no-install-project --no-editable"
    )
    build_sync = (
        'SETUPTOOLS_SCM_PRETEND_VERSION="${XAGENT_PACKAGE_VERSION}" \\\n'
        "    VIRTUAL_ENV=/opt/venv uv sync --active --locked --no-dev --no-editable"
    )
    assert dependency_sync in dockerfile
    assert build_sync in dockerfile
    assert "COPY .git .git" not in dockerfile


def test_backend_dockerfile_uses_uv_deployment_sync() -> None:
    dockerfile = read_repo_file("docker/Dockerfile.backend")

    assert "uv pip compile" not in dockerfile
    assert "uv pip sync" not in dockerfile
    assert "--no-emit-package xagent" not in dockerfile
    assert dockerfile.count("uv sync") == 2
    assert dockerfile.count("--active") == 2
    assert dockerfile.count("--locked") == 2
    assert dockerfile.count("--no-dev") == 2
    assert dockerfile.count("--no-editable") == 2
    assert dockerfile.count("--group backend-image") == 2
    assert "--torch-backend" not in dockerfile
    assert "--no-install-project" in dockerfile
    assert "COPY pyproject.toml uv.lock README.md ./" in dockerfile
    assert "ENV UV_COMPILE_BYTECODE=1" in dockerfile
    assert "ENV UV_LINK_MODE=copy" in dockerfile
    assert "ENV UV_PYTHON_DOWNLOADS=0" in dockerfile


def test_backend_image_dependencies_are_deployment_group() -> None:
    pyproject = read_repo_file("pyproject.toml")
    optional_dependencies = pyproject_section(
        pyproject, "project.optional-dependencies"
    )
    dependency_groups = pyproject_section(pyproject, "dependency-groups")

    assert "backend-image = [" not in optional_dependencies
    assert "backend-image = [" in dependency_groups
    assert '"torch"' in dependency_groups
    assert '"torchvision"' in dependency_groups


def test_pytorch_cpu_index_is_project_configured_for_uv_sync() -> None:
    pyproject = read_repo_file("pyproject.toml")

    assert 'torch = [{ index = "pytorch-cpu" }]' in pyproject
    assert 'torchvision = [{ index = "pytorch-cpu" }]' in pyproject
    assert 'name = "pytorch-cpu"' in pyproject
    assert 'url = "https://download.pytorch.org/whl/cpu"' in pyproject
    assert "explicit = true" in pyproject


def test_boxlite_is_not_declared_for_linux_aarch64() -> None:
    pyproject = tomllib.loads(read_repo_file("pyproject.toml"))
    project_requirements = [
        Requirement(dependency) for dependency in pyproject["project"]["dependencies"]
    ]
    boxlite_requirements = [
        requirement
        for requirement in project_requirements
        if canonicalize_name(requirement.name) == "boxlite"
    ]

    def supported(platform: str, machine: str) -> bool:
        environment = {"sys_platform": platform, "platform_machine": machine}
        return any(
            requirement.marker is None or requirement.marker.evaluate(environment)
            for requirement in boxlite_requirements
        )

    assert not supported("linux", "aarch64")
    assert supported("linux", "x86_64")
    assert supported("darwin", "arm64")


def test_publish_script_derives_package_version_from_valid_tags() -> None:
    publish_script = read_repo_file("docker/publish.sh")

    assert 'DEFAULT_PACKAGE_VERSION="${TAG#v}"' in publish_script
    assert (
        'PACKAGE_VERSION="${XAGENT_PACKAGE_VERSION:-${DEFAULT_PACKAGE_VERSION}}"'
        in publish_script
    )
    assert 'DEFAULT_PACKAGE_VERSION="0.0.0+${GIT_COMMIT::12}"' in publish_script
    assert 'XAGENT_VERSION="${XAGENT_VERSION:-${TAG}}"' in publish_script
    assert (
        'python "${REPO_ROOT}/scripts/write_package_version.py"' not in publish_script
    )
    assert '--build-arg "XAGENT_PACKAGE_VERSION=${PACKAGE_VERSION}"' in publish_script


def test_backend_dockerfile_uses_frontend_managed_pptxgenjs() -> None:
    dockerfile = read_repo_file("docker/Dockerfile.backend")
    package_json = read_repo_file("frontend/package.json")

    assert '"pptxgenjs": "4.0.1"' in package_json
    assert "npm install -g pptxgenjs" not in dockerfile
    assert "/usr/lib/node_modules/pptxgenjs" not in dockerfile
    assert 'ENV NODE_PATH="/opt/xagent/frontend/node_modules"' in dockerfile


def test_backend_runtime_keeps_uv_binaries() -> None:
    dockerfile = read_repo_file("docker/Dockerfile.backend")

    assert dockerfile.count("COPY --from=uv /uv /uvx /usr/local/bin/") == 2


def test_backend_dockerfile_installs_docker_cli_without_the_daemon() -> None:
    """docker.io ships an engine + containerd + runc that a daemonless
    container never uses, but /usr/bin/docker must survive the swap:
    command_policy.py gates the agent shell by path, not by an allowlist.
    See https://github.com/xorbitsai/xagent/pull/1807
    """

    dockerfile = read_repo_file("docker/Dockerfile.backend")

    # `runtime` is FROM runtime-base and copies no apt layer out of the build
    # stages, so an install that drifted into backend-base would ship an image
    # with no /usr/bin/docker at all.
    runtime_base = dockerfile.split(
        "FROM python:${PYTHON_VERSION}-bookworm AS runtime-base\n", maxsplit=1
    )[1].split("\nFROM ", maxsplit=1)[0]
    block = next(
        chunk for chunk in runtime_base.split("\n\n") if "docker-ce-cli" in chunk
    )
    install = "\n".join(line for line in block.splitlines() if not line.startswith("#"))

    # Hardcoding an arch here would break the arm64 image (docker-publish.yml
    # publishes amd64 + arm64); --no-install-recommends keeps
    # docker-buildx-plugin/docker-compose-plugin out.
    assert (
        "arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker.gpg"
        in install
    )
    assert "--no-install-recommends docker-ce-cli \\" in install
    for daemon_package in ("docker.io", "docker-ce ", "containerd", "runc"):
        assert daemon_package not in install

    # The key and source list must be dropped by the same layer that added
    # them, or download.docker.com stays resolvable in the running container.
    assert (
        "rm -rf /var/lib/apt/lists/* /etc/apt/sources.list.d/docker.list"
        " /usr/share/keyrings/docker.gpg" in install
    )
    # Smoke-tested during the build so a broken swap fails on both published
    # architectures instead of shipping.
    assert "test -x /usr/bin/docker \\" in install
    assert "! command -v dockerd \\" in install


def test_backend_package_version_is_vcs_based_for_normal_builds() -> None:
    pyproject = read_repo_file("pyproject.toml")

    assert 'dynamic = ["version"]' in pyproject
    assert 'requires = ["hatchling", "hatch-vcs"]' in pyproject
    assert 'source = "vcs"' in pyproject
    assert 'path = "src/xagent/_version.py"' not in pyproject
    assert not (ROOT / "src" / "xagent" / "_version.py").exists()
    assert not (ROOT / "scripts" / "write_package_version.py").exists()


def test_docker_workflows_pass_package_version_to_backend_build() -> None:
    release_workflow = read_workflow("docker-publish.yml")
    nightly_workflow = read_workflow("nightly-build.yml")

    assert (
        'python scripts/write_package_version.py "$PACKAGE_VERSION"'
        not in release_workflow
    )
    assert (
        'python scripts/write_package_version.py "$PACKAGE_VERSION"'
        not in nightly_workflow
    )
    assert (
        "XAGENT_PACKAGE_VERSION=${{ steps.version-meta.outputs.package_version }}"
        in release_workflow
    )
    assert (
        "XAGENT_PACKAGE_VERSION=${{ steps.version-meta.outputs.package_version }}"
        in nightly_workflow
    )


def test_sandbox_group_covers_runtime_requirements_with_compatible_lock() -> None:
    runtime_constants = {
        "src/xagent/core/tools/adapters/vibe/sandboxed_tool/"
        "sandboxed_tool_wrapper.py": "SANDBOX_BASE_DEPENDENCIES",
        "src/xagent/core/tools/adapters/vibe/sandboxed_tool/"
        "sandboxed_mcp_tool_helper.py": "_MCP_SANDBOX_EXTRA_PACKAGES",
    }
    runtime_requirements: list[str] = []
    for path, constant_name in runtime_constants.items():
        module = ast.parse(read_repo_file(path))
        assignment = next(
            node
            for node in module.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == constant_name
                for target in node.targets
            )
        )
        runtime_requirements.extend(ast.literal_eval(assignment.value))

    pyproject = tomllib.loads(read_repo_file("pyproject.toml"))
    sandbox_requirements = {
        canonicalize_name(requirement.name): requirement
        for raw_requirement in pyproject["dependency-groups"]["sandbox"]
        if (requirement := Requirement(raw_requirement))
    }
    lock = tomllib.loads(read_repo_file("uv.lock"))
    locked_versions = {
        canonicalize_name(package["name"]): Version(package["version"])
        for package in lock["package"]
        if "version" in package
    }

    for raw_requirement in runtime_requirements:
        runtime_requirement = Requirement(raw_requirement)
        name = canonicalize_name(runtime_requirement.name)
        assert name in sandbox_requirements
        assert locked_versions[name] in runtime_requirement.specifier


def test_backend_mcp_dependency_excludes_2x() -> None:
    # Companion to test_sandbox_group_covers_runtime_requirements_with_compatible_lock
    # above, which only guards the sandbox group's mcp bound: mcp 2.0 removed
    # mcp.client.streamable_http.streamablehttp_client, which the backend's own
    # sessions.py imports eagerly, so [project].dependencies must independently
    # keep mcp below 2.x even if that guard alone were ever satisfied.
    pyproject = tomllib.loads(read_repo_file("pyproject.toml"))
    backend_requirements = {
        canonicalize_name((requirement := Requirement(raw)).name): requirement
        for raw in pyproject["project"]["dependencies"]
    }

    mcp_requirement = backend_requirements[canonicalize_name("mcp")]
    assert Version("2.0.0") not in mcp_requirement.specifier


def test_sandbox_dockerfile_installs_locked_group_and_smoke_tests_imports() -> None:
    dockerfile = read_repo_file("docker/Dockerfile.sandbox")

    assert "FROM python:3.11-slim AS sandbox-requirements" in dockerfile
    assert "COPY pyproject.toml uv.lock ./" in dockerfile
    assert (
        "uv export --quiet --locked --only-group sandbox --no-emit-project"
        in dockerfile
    )
    assert "FROM node:22-slim AS sandbox" in dockerfile
    runtime_stage = dockerfile.split("FROM node:22-slim AS sandbox\n", maxsplit=1)[1]
    assert "COPY pyproject.toml uv.lock ./" not in runtime_stage
    assert "COPY --from=sandbox-requirements /sandbox-requirements.txt" in runtime_stage
    assert "uv pip install --system --break-system-packages" in runtime_stage
    assert "docker/sandbox-requirements.txt" not in dockerfile
    assert not (ROOT / "docker" / "sandbox-requirements.txt").exists()
    assert "USER sandbox" in runtime_stage


def test_sandbox_runtime_keeps_and_smoke_tests_uvx() -> None:
    dockerfile = read_repo_file("docker/Dockerfile.sandbox")
    runtime_stage = dockerfile.split("FROM node:22-slim AS sandbox\n", maxsplit=1)[1]

    assert "COPY --from=uv /uv /uvx /usr/local/bin/" in runtime_stage
    assert "uvx --version" in runtime_stage


def test_sandbox_export_is_locked_without_managed_python_downloads() -> None:
    dockerfile = read_repo_file("docker/Dockerfile.sandbox")
    requirements_stage = dockerfile.split(
        "FROM python:3.11-slim AS sandbox-requirements\n", maxsplit=1
    )[1].split("FROM node:22-slim AS sandbox\n", maxsplit=1)[0]

    assert "ENV UV_PYTHON_DOWNLOADS=0" in requirements_stage
    assert "uv export --quiet --locked --only-group sandbox --no-emit-project" in (
        requirements_stage
    )


def test_sandbox_npm_cache_is_owned_by_the_boxlite_runtime_user() -> None:
    dockerfile = read_repo_file("docker/Dockerfile.sandbox")

    assert "ENV NPM_CONFIG_CACHE=/opt/npm-cache" in dockerfile
    assert 'chmod 0755 "$NPM_CONFIG_CACHE"' in dockerfile
    assert 'chown -R 1100:1010 "$NPM_CONFIG_CACHE"' in dockerfile
    assert (
        "CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS=1 NPM_CONFIG_OFFLINE=true" in dockerfile
    )
    assert "npx -y --offline chrome-devtools-mcp@1.6.0 --help" in dockerfile


def test_sandbox_uv_install_uses_buildkit_cache() -> None:
    dockerfile = read_repo_file("docker/Dockerfile.sandbox")
    runtime_stage = dockerfile.split("FROM node:22-slim AS sandbox\n", maxsplit=1)[1]

    assert "--mount=type=cache,target=/root/.cache/uv" in runtime_stage
    assert "--no-cache" not in runtime_stage


def test_uv_export_locked_sandbox_group_contains_direct_distributions() -> None:
    result = subprocess.run(
        [
            "uv",
            "export",
            "--quiet",
            "--locked",
            "--only-group",
            "sandbox",
            "--no-emit-project",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    project = tomllib.loads(read_repo_file("pyproject.toml"))
    exported = {
        canonicalize_name(Requirement(line.rstrip(" \\")).name)
        for line in result.stdout.splitlines()
        if line and not line.startswith((" ", "#", "-"))
    }
    required = {
        canonicalize_name(Requirement(requirement).name)
        for requirement in project["dependency-groups"]["sandbox"]
    }
    assert required <= exported


def sandbox_publish_step(name: str) -> dict[str, Any]:
    workflow = yaml.safe_load(read_workflow("sandbox-publish.yml"))
    for step in workflow["jobs"]["build-and-push"]["steps"]:
        if step.get("name") == name:
            return cast(dict[str, Any], step)
    raise AssertionError(f"sandbox-publish.yml has no step named {name!r}")


def test_sandbox_hub_description_step_is_sha_pinned_to_the_sandbox_repository() -> None:
    step = sandbox_publish_step("Update Docker Hub repository description")

    assert step["uses"] == (
        "peter-evans/dockerhub-description@1b9a80c056b620d92cedb9d9b5a223409c68ddfa"
    )
    assert step["with"]["repository"] == "${{ env.SANDBOX_IMAGE }}"
    assert step["with"]["readme-filepath"] == "./docker/README.sandbox.md"
    assert step["with"]["username"] == "${{ secrets.DOCKERHUB_USERNAME }}"
    assert step["with"]["password"] == "${{ secrets.DOCKERHUB_PASSWORD }}"


# WHY: the Hub description is repository-global, not tag-scoped, so a
# push_to_dockerhub=false dry run must not reach it.
def test_sandbox_hub_description_gate_equals_the_image_push_gate() -> None:
    description = sandbox_publish_step("Update Docker Hub repository description")
    build = sandbox_publish_step("Build and push sandbox image")

    assert description["if"] == build["with"]["push"]
    assert "push_to_dockerhub == 'true'" in description["if"]


def test_sandbox_hub_description_failure_does_not_fail_the_release() -> None:
    step = sandbox_publish_step("Update Docker Hub repository description")

    assert step["continue-on-error"] is True


# WHY: the action truncates an oversized readme or short description and only
# warns, so drift past a Hub limit ships silently with the run still green.
def test_sandbox_hub_description_inputs_fit_docker_hub_limits() -> None:
    step = sandbox_publish_step("Update Docker Hub repository description")

    readme = ROOT / step["with"]["readme-filepath"]
    assert readme.is_file()
    assert len(readme.read_bytes()) <= 25_000

    short_description = step["with"]["short-description"]
    assert short_description
    assert len(short_description.encode("utf-8")) <= 100
