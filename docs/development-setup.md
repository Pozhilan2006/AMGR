# Development setup

## Python backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Node frontend

```bash
cd frontend
npm install
npm run dev -- --hostname 0.0.0.0
```

## Database

```bash
docker compose up -d db
```

## Migrations

```bash
cd backend
alembic upgrade head
```

## Compose environment

For local compose runs, the database host should be `db` from the backend container. The browser-facing frontend should use `localhost` when it accesses the backend through Docker or direct host execution.
