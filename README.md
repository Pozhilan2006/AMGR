# AMGR

AMGR (Autonomous Multi-Agent Governance Runtime) is a runtime information-flow control plane for AI systems. It captures interactions between instrumented AI agents, models, tools, databases, APIs, and external services; assembles a flow graph and provenance history; applies deterministic policy logic; and surfaces outcomes through a dashboard and an AI Security Operator.

This repository is intentionally structured as a small monorepo foundation for parallel team work. It preserves a Python backend, a TypeScript/Next.js frontend, and a lightweight Python SDK while clearly separating responsibilities and placeholder areas that are not yet implemented.

## Architecture overview

The runtime flow is:

Instrumented application -> SDK capture -> event ingestion -> graph and lineage -> policy evaluation -> enforcement decision -> dashboard and AI Security Operator

The system is split into three primary development surfaces:

- Backend: FastAPI service, SQLAlchemy persistence, Alembic migrations, Pydantic schemas, and policy enforcement boundaries.
- Frontend: Next.js dashboard shell and placeholder screens for graph, policies, decisions, approvals, and operator views.
- SDK: lightweight Python client and metadata abstractions for event capture, correlation context, and authorization hooks.

## Current implementation status

This repository contains the startup architecture and deliberately honest placeholders. The following areas are scaffolded but not fully implemented:

- Event ingestion endpoints
- Graph query and lineage services
- Deterministic policy evaluation
- Authorization and approval processing
- AI Security Operator provider integration
- Dashboard live data wiring

## Prerequisites

- Python 3.11+
- Node.js 22+
- Docker Desktop or Docker Engine with Compose
- PostgreSQL 16 (via Docker Compose by default)
- Git for working with branches and reviews

## Local setup

1. Copy the example environment files:
   - `cp .env.example .env`
   - `cp frontend/.env.example frontend/.env.local`
2. Install the Python packages and frontend dependencies:
   - `python -m pip install -e ./backend -e ./sdk`
   - `cd frontend && npm install`
3. Start the database:
   - `docker compose up -d db`
4. Start the backend and frontend in separate terminals:
   - `make backend`
   - `make frontend`

## Environment configuration

The repository uses environment variables to avoid hardcoded secrets. The root `.env.example` file contains the base configuration for Compose and local services.

Required variables:

- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`
- `DATABASE_URL`
- `API_V1_PREFIX`
- `APP_ENV`
- `NEXT_PUBLIC_API_BASE_URL`

Do not commit `.env` files, database dumps, or secrets.

## Startup commands

```bash
# Start all services with Docker Compose
docker compose up --build

# Stop services
docker compose down -v

# Run backend locally
make backend

# Run frontend locally
make frontend

# Run Alembic migrations
make migrate
```

## Database migrations

The backend uses Alembic and SQLAlchemy metadata. Run the migrations from the backend directory:

```bash
cd backend
alembic upgrade head
```

To create a new migration after updating model metadata:

```bash
cd backend
alembic revision --autogenerate -m "describe_change"
```

## Repository structure

- `backend/` - FastAPI service, database config, and domain modules
- `frontend/` - Next.js dashboard and placeholder screens
- `sdk/` - Python package for capture, transport, context, and enforcement interfaces
- `docs/` - architecture and team contract documentation
- `examples/` - starter example app code

## Team ownership boundaries

The project is organized for a five-member team. Ownership is documented in `docs/team-ownership.md` and includes shared boundaries for schemas, routes, API contracts, and database models.

## Important design notes

- The enforcement hook must exist at a protected operation boundary for runtime blocking to work.
- Route handlers intentionally return explicit `501 Not Implemented` responses where the feature is deliberately out of scope for the starter release.
- The SDK avoids network and policy execution in starter code; it defines the contracts and integration points for future implementation.

## License and contribution

This repository is a starter architecture for AMGR development and is not a production deployment. Prior to production use, the team should add secure key management, operational monitoring, authentication, and production-grade policy enforcement.
