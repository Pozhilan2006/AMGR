.PHONY: up down backend frontend migrate install

up:
	docker compose up --build

down:
	docker compose down -v

install:
	python -m pip install -e ./backend -e ./sdk
	cd frontend && npm install

backend:
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

frontend:
	cd frontend && npm install && npm run dev -- --hostname 0.0.0.0

migrate:
	cd backend && alembic upgrade head

status:
	git status --short
