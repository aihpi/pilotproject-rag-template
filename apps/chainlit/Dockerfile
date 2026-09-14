FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Docling's layout model (docling-ibm-models 3.13) asks torch.compile to build a kernel,
# which needs a C++ compiler this slim image does not have; every PDF conversion then
# fails with "InvalidCxxCompiler". Plain eager execution converts a paper in ~20 s on CPU.
ENV TORCH_COMPILE_DISABLE=1

# The venv lives OUTSIDE /app on purpose: docker-compose bind-mounts the repo over /app
# at runtime, which would shadow a /app/.venv — and the developer's committed .venv is
# macOS-native, so the container would end up importing incompatible binaries.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
# Hardlinking across the build filesystem is unreliable; copy instead. No cache layer,
# so the uv download cache does not end up baked into the image.
ENV UV_LINK_MODE=copy
ENV UV_NO_CACHE=1

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.9.14 /uv /usr/local/bin/uv

# A LOGIN shell re-sources /etc/profile, which resets PATH and would drop /opt/venv/bin —
# leaving `python` as the dependency-free system interpreter. The ingest service in
# docker-compose.yml runs `sh -lc`, so without this its `python -m kb.ingest` would die on
# ModuleNotFoundError. Cover login shells as well as the ENV PATH above.
RUN printf 'export PATH=/opt/venv/bin:$PATH\n' > /etc/profile.d/10-venv.sh

# Install from uv.lock so Docker, CI and local dev resolve identically. Resolving freely
# here is what let FastAPI drift past 0.136 — silently shadowing every custom route
# behind Chainlit's SPA catch-all — and let docling pull a GUI OpenCV that crashed on
# `import cv2`. --frozen fails loudly if pyproject.toml and uv.lock ever drift apart.
#
# The lockfile also carries the two build-shaping decisions, so nothing is installed and
# then thrown away here: torch/torchvision resolve from the CPU wheel index (no ~4.4 GB
# of unusable nvidia-*/triton), and opencv-python-headless stands in for rapidocr's GUI
# opencv-python. See the [tool.uv] blocks in pyproject.toml.
#
# Dependencies first, without the project itself → cached unless the manifests change.
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev --no-install-project

# App code + static assets last → fast rebuilds on CSS/JS changes. The second sync only
# installs the project itself, which is cheap.
COPY . /app
RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["chainlit", "run", "app.py", "--host", "0.0.0.0", "--port", "8000"]
