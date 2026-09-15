# The evaluation service. Deliberately NOT built from the app Dockerfile: this image
# installs only the `eval` dependency group, so it carries ragas + openai + fastapi
# and none of chainlit, docling or torch. Keeping the two apart is the whole point of
# running scoring out of process — see eval_app/main.py.
#
# Build context is apps/chainlit (it needs pyproject.toml and uv.lock), so this is
# referenced from docker-compose.yml as `dockerfile: eval_app/Dockerfile`.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Same reasoning as the app image: the venv lives outside /app because compose
# bind-mounts the repo over it, which would shadow a /app/.venv.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV UV_LINK_MODE=copy
ENV UV_NO_CACHE=1
# uv's HTTP timeout is a READ timeout: how long it waits for the next chunk of an
# already-open stream, not a cap on total download time. The default 30 s is easy to
# miss on a shared link, where a small wheel queued behind torch (~183 MB) can sit
# silent for longer, at which point uv drops it and the whole atomic sync with it.
# UV_NO_CACHE above means the retry then starts from zero. Neither setting does
# anything on a fast link, where no stream ever idles.
#
# ARG, not a bare ENV: a Docker build does not inherit the shell that invoked it, so
# `UV_HTTP_TIMEOUT=1800 docker compose build` sets the variable on the compose client
# and the build still uses this value. The args: block in docker-compose.yml forwards
# it, which is what makes the command in docs/troubleshooting.md do anything. The
# defaults below are what a build with nothing set gets.
#
# Concurrency is also capped in [tool.uv] in pyproject.toml, which this image copies
# before syncing; that covers a local `uv sync` too. The env var overrides it.
ARG UV_HTTP_TIMEOUT=300
ARG UV_CONCURRENT_DOWNLOADS=8
ENV UV_HTTP_TIMEOUT=${UV_HTTP_TIMEOUT}
ENV UV_CONCURRENT_DOWNLOADS=${UV_CONCURRENT_DOWNLOADS}

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.9.14 /uv /usr/local/bin/uv

# --only-group installs the group WITHOUT the project's own dependencies, which is what
# keeps this image slim: RAGAS pulls langchain and datasets, and none of that belongs in
# the app image. --frozen fails loudly if pyproject.toml and uv.lock drift apart, so the
# eval group must be present in the lockfile (`uv lock`).
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-install-project --only-group eval

# Only the service's own code and its one static page.
COPY eval_app /app/eval_app

# Run as an unprivileged user. The eval DB volume mounts at /app/.evaldb, so the
# user must own that directory — created here so the ownership survives even when
# the volume is empty on first start.
RUN useradd --system --no-create-home eval \
 && mkdir -p /app/.evaldb \
 && chown eval:eval /app/.evaldb
USER eval

EXPOSE 8001

CMD ["uvicorn", "eval_app.main:app", "--host", "0.0.0.0", "--port", "8001"]
