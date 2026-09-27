# Xagent Web API

## Overview

The Xagent Web API provides a FastAPI-based backend service for managing and executing AI agents. It offers RESTful endpoints and WebSocket connections for real-time agent execution monitoring and interaction.

## Features

### Agent Management
- **Agent Execution**: Create and manage agent execution tasks
- **Execution History**: Track and retrieve agent execution history
- **Nested Agents**: Support for hierarchical agent execution with parent-child relationships

### Real-time Communication
- **WebSocket Support**: Real-time agent execution monitoring and streaming
- **Event Streaming**: Live updates on agent status, tool calls, and results
- **Interactive Control**: Send commands and feedback during agent execution

### File Management
- **File Upload**: Upload files for agent processing
- **File Storage**: Organized file storage with workspace isolation
- **File Operations**: Read, write, and manage files through the API

### Visualization
- **DAG Visualization**: Generate and visualize agent execution graphs
- **Execution Flow**: Track agent execution flow and dependencies

### Observability
- **Optional self-hosted Langfuse**: Off by default. Set `LANGFUSE_TRACING_ENABLED=true`, real `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`, and an explicit self-hosted HTTP(S) `LANGFUSE_BASE_URL` (or `LANGFUSE_HOST`) to opt in. Langfuse Cloud is not a default or supported target.
- **Execution Metrics**: Track performance and resource usage
- **Error Tracking**: Comprehensive error logging and reporting

## API Endpoints

- `POST /api/auth/setup-admin` - Create the first local password-login administrator
- `POST /api/auth/login` - Sign in with local credentials
- `POST /api/agents` / `GET /api/agents/{agent_id}` - Manage saved agents
- `WS /ws/chat/{task_id}` - Stream an authenticated task conversation
- `POST /api/files/upload` / `GET /api/files/download/{file_id}` - Upload and download files
- `/api/skills/*` - Local installed-skill authoring (see [skills README](../skills/README.md#local-skills-ui-and-api))

See the running server's `/docs` for request/response schemas and the remaining
registered routes. Public chat bots, cloud catalogs and Gmail provisioning are
not part of this API.

## Installation

Install the current-source application and dependencies, not a hand-picked
subset of FastAPI packages. For an isolated LAN, build the current-source wheel
and prepare offline assets on a connected host as described in the
[Docker and wheel deployment guide](../../../docker/README.md#isolated-lan-deployment-prepared-images-and-assets).

## Running the Server

Start the web API server:

```bash
python -m xagent.web
# Or, after installing the current-source wheel: xagent
```

The default endpoint is `http://127.0.0.1:8000`; use `--host` and `--port`
for another listener. The first user creates an administrator at `/setup`
and signs in with a local password.

### API Documentation

Once the server is running, access the interactive API documentation:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## WebSocket

`/ws/chat/{task_id}` requires authenticated access to that task. Use the
browser client or the server's current WebSocket contract rather than the
retired `/ws/agent/{agent_id}` example.

## Development

### Project Structure

`__main__.py` launches the server, `app.py` registers routers, and `api/`
contains HTTP and WebSocket route handlers (`agents.py`, `files.py`,
`websocket.py`, `skills.py`, and others). Add new routers in `api/` and register
them in `app.py` following the existing handlers.

## Configuration

Configuration is documented in the root `example.env`; the CLI accepts
`--host` (default `127.0.0.1`), `--port` (default `8000`),
`--reload` and `--log-level`. `XAGENT_MAX_UPLOAD_SIZE` limits each backend
upload (bytes or values like `100M`). The bundled nginx also has a separate
500M `client_max_body_size` ceiling; requests above it never reach the backend.

## Testing

Run the web API tests:
```bash
pytest tests/web/
```

## Production Deployment

Build and deploy the current-source image or wheel and the prepared assets
as described in the [deployment guide](../../../docker/README.md). Put
browser access behind HTTPS/TLS outside loopback, configure private secrets
and local password accounts, and enable optional self-hosted tracing only
when its endpoint and credentials are explicitly configured.
