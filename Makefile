.PHONY: setup lint typecheck test test-integration run-mcp run-api up down build evals

# Requires Python 3.12+. Override with e.g. `make setup PYTHON=/path/to/python3.12`.
PYTHON ?= python3.12
VENV := .venv
BIN := $(VENV)/bin
PY := $(BIN)/python

setup:
	$(PYTHON) -m venv $(VENV) && $(PY) -m pip install -e ".[dev]"
	@test -f .env || cp .env.example .env

lint:
	$(BIN)/ruff check . && $(BIN)/ruff format --check .

typecheck:
	$(BIN)/mypy app mcp_server

test:
	$(BIN)/pytest

test-integration:
	$(BIN)/pytest -m integration

run-mcp:
	$(BIN)/uvicorn mcp_server.server:app --env-file .env --host 0.0.0.0 --port 8001 --reload

run-api:
	$(BIN)/uvicorn app.main:create_app --factory --env-file .env --host 0.0.0.0 --port 8000 --reload

up:
	docker compose up --build

down:
	docker compose down

build:
	docker build --target api -t nl-assistant-api .
	docker build --target mcp -t nl-assistant-mcp .

evals:
	$(PY) scripts/run_evals.py --base-url $${BASE_URL:-http://localhost:8000}
