# rensim on the web (renaissance.hosted): see docs/hosting.md. The players' folders and the
# registry live in /data: mount a volume there.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.11.32
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
# Dependencies first, so changing the code doesn't reinstall them.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-install-project
COPY renaissance ./renaissance
RUN uv sync --frozen

RUN useradd --create-home rensim && mkdir /data && chown rensim /data
USER rensim
ENV PATH=/app/.venv/bin:$PATH RENSIM_ROOT=/data RUNTIME__DLTHUB_TELEMETRY=false PORT=8000
VOLUME /data
EXPOSE 8000
CMD ["rensim-hosted"]
