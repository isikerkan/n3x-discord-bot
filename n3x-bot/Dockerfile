FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project
COPY n3x_bot ./n3x_bot
RUN uv sync --no-dev

RUN useradd -m botuser && mkdir -p /app/data && chown -R botuser /app
USER botuser

CMD ["uv", "run", "python", "-m", "n3x_bot"]
