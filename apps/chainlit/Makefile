.PHONY: help init-env up check logs down ps reingest local-install local-run

help:
	@echo "Targets:"
	@echo "  make init-env      # create .env from .env.example if missing"
	@echo "  make up            # docker compose up -d --build"
	@echo "  make check         # test the AI service + search index are reachable"
	@echo "  make logs          # follow chainlit logs"
	@echo "  make down          # stop stack"
	@echo "  make ps            # list compose services"
	@echo "  make reingest      # force recreate qdrant collection + restart chainlit"
	@echo "  make local-install # install app into .venv with uv (not pip)"
	@echo "  make local-run     # run chainlit locally"

init-env:
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example"; else echo ".env already exists"; fi

up: init-env
	docker compose up -d --build

# No --no-deps here: check_setup verifies the search index, so qdrant has to be
# running. The ingest service already declares depends_on: qdrant, so Compose
# starts it, and only it: ingest does not depend on postgres.
check: init-env
	docker compose run --rm ingest python -m check_setup

logs:
	docker compose logs -f chainlit

down:
	docker compose down

ps:
	docker compose ps

reingest:
	INGEST_RECREATE=true docker compose run --rm ingest
	docker compose up -d chainlit

# uv, not pip: the CPU-only torch and headless-OpenCV choices live in [tool.uv] in
# pyproject.toml, which pip ignores. `pip install -e .` pulls the CUDA build and both
# OpenCV wheels, which breaks `import cv2` on slim/headless machines.
local-install:
	uv sync

local-run:
	. .venv/bin/activate && chainlit run app.py -w
