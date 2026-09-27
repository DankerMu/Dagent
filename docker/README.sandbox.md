# Xagent Sandbox

Sandbox runtime image for [Xagent](https://github.com/xorbitsai/xagent), an open-source framework for building and running AI agents. With sandboxing enabled, Xagent delegates untrusted work — generated Python and JavaScript, shell commands, `npx`/`uvx` MCP servers — to a sandbox built from this image rather than running it in the backend process.

A sandbox is a **named, stateful workspace**, not a throwaway container. Xagent creates one on demand, keeps it alive, and execs into it for each subsequent tool call. Stopping a sandbox preserves its filesystem; the next request resumes it. On the Docker backend, Xagent can also commit a sandbox's filesystem to a snapshot and use that snapshot — rather than this image — as the template for a new one; the Boxlite backend does not support snapshots.

This image is that starting template. It has no service of its own: its `CMD` is a plain `bash`, which the Docker backend replaces with a long-running idle process.

## What's inside

- **Node.js 22** (`node:22-slim` base) and **Python 3.11**, both on `PATH` as `node` and `python`
- **`uv` / `uvx`**, for sandboxed `uvx` MCP server connections
- A deliberately small, lockfile-pinned Python set: `pydantic`, `pydantic-settings`, `cloudpickle`, `mcp`, `pandas`, `numpy`, `matplotlib`, `openpyxl`, `python-docx`, `fsspec`, `bashlex`
- `ca-certificates`, `tzdata`, `netbase`, and `openssh-client`
- Built-in Python dependencies are prepared from `uv.lock` during the online image build; runtime pip verifies them with `--no-index`. Optional extra packages require a rebuilt image or an explicit private LAN mirror (`XAGENT_SANDBOX_PIP_INDEX_URL`).
- **`pptxgenjs@4.0.1`** and its npm cache are prepared during the build. JavaScript tools use cache-only installation with lifecycle scripts disabled; other packages must be prepared in a custom image before deployment.

The Python set comes only from the dedicated `sandbox` dependency group in Xagent's `pyproject.toml`, exported from `uv.lock` at build time. It intentionally does not inherit the backend image's much larger dependency set, and the build fails if any supported package is missing from the result.

## Isolation

Sandboxing is opt-in: Xagent runs tool calls in the backend process unless `SANDBOX_ENABLED=true`, and falls back to the backend when no sandbox backend is available or a tool cannot be wrapped. What follows applies to work that does reach a sandbox.

The isolation comes from how the sandbox is run, not from this image. Xagent's Docker backend applies `no-new-privileges`, CPU and memory limits, and optional network isolation; its Boxlite backend runs the sandbox inside a KVM microVM. See [docker/README.md](https://github.com/xorbitsai/xagent/blob/main/docker/README.md) for the two modes and their trade-offs.

The image itself defines an unprivileged `sandbox` user (uid 1100, gid 1010) as its default. Note that Xagent's Docker backend deliberately overrides this and runs the container as root, to match the file access behavior of the Boxlite backend.

## Tags

- `latest` — the most recently published build
- `X.Y.Z` — published by pushing the matching Git tag, though a manual run can publish an arbitrary tag; see [Xagent releases](https://github.com/xorbitsai/xagent/releases)

Built for `linux/amd64` and `linux/arm64`.

Use an explicit current-source local tag in production; the Compose overlays default to `xagent-lan-sandbox:local`. Changing a tag is not a free rollback: Xagent reconciles running sandboxes against the new image spec, stopping, deleting and recreating them. Bind-mounted workspace and upload data survives; the container's writable layer does not. Drain or back up that state before switching tags.

## Usage

Set `SANDBOX_IMAGE=xagent-lan-sandbox:local` outside Compose too. Build the
current source on the connected host and transfer that image; an upstream
release does not contain this checkout's offline prerequisites. Docker sandbox
creation refuses a missing image rather than pulling it.

```bash
docker build -f docker/Dockerfile.sandbox -t xagent-lan-sandbox:local .
docker save xagent-lan-sandbox:local -o sandbox-image.tar
docker load -i sandbox-image.tar  # on the isolated host
```

Boxlite does not consume Docker's daemon cache. On the connected host,
export the preloaded image to an OCI layout:

```bash
skopeo copy docker-daemon:xagent-lan-sandbox:local oci:./sandbox-oci:local
```

Copy the layout directory to the deployment host. Set
`XAGENT_BOXLITE_ROOTFS_PATH` to that local path; the Boxlite Compose overlay
mounts it from `XAGENT_BOXLITE_OCI_HOST_PATH` before the backend starts.
The guest layout is not the SDK bootstrap cache. Prepare the separate pinned
BoxLite Debian bootstrap cache for every execution home as described in
[the offline deployment guide](README.md#isolated-lan-deployment-prepared-images-and-assets).

### Native integration test prerequisites

The guest test suites require `pytest` and `pytest-asyncio` baked into a separate
test image during connected preparation; they never install packages at runtime.
For the current lockfile, derive the test image with:

```dockerfile
FROM xagent-lan-sandbox:local
USER root
RUN uv pip install --system --break-system-packages pytest==9.0.2 pytest-asyncio==1.3.0
USER sandbox
```

Export that test image to OCI and set `XAGENT_BOXLITE_ROOTFS_PATH` to it.
Set `XAGENT_TEST_BOXLITE_BOOTSTRAP_OCI` to the transferred Debian bootstrap OCI.
The tests prepare separate short temporary native homes; they do not read or
modify the operator's BoxLite home. The production image does not need pytest.

## Building a custom sandbox image

A replacement image has to stay runtime-compatible with [`docker/Dockerfile.sandbox`](https://github.com/xorbitsai/xagent/blob/main/docker/Dockerfile.sandbox), which is the easiest starting point. Every sandbox needs, on `PATH`:

- `python` and `node` — tool code runs as `python -c ...` and `node -e ...`
- `pip` — verifies image-baked Python dependencies without a public index; extra packages require an explicit LAN mirror or a custom image
- `cat`, `rm`, `mkdir`, `/bin/sh`, and a writable `/tmp` — staging input, reading results back, cleanup
- `tail` — the Docker backend replaces the image's `CMD` with `tail -f /dev/null` to hold the container open
- `test`, `cp`, `mv`, and a writable `/var/tmp` — the Boxlite backend stages every file transfer there before moving it into place, because `/tmp` is a tmpfs mount it cannot copy into

Only if you use the matching feature:

- `npx` — sandboxed `npx` MCP servers
- `uvx` — sandboxed `uvx` MCP servers; Xagent no longer installs `uv` dynamically

## Links

- [Source and documentation](https://github.com/xorbitsai/xagent)
- [Dockerfile](https://github.com/xorbitsai/xagent/blob/main/docker/Dockerfile.sandbox)
- [Issue tracker](https://github.com/xorbitsai/xagent/issues)
