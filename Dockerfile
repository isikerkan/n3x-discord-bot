FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY n3x_bot ./n3x_bot
RUN uv sync --no-dev

RUN useradd -m botuser && chown -R botuser /app
USER botuser

CMD ["uv", "run", "python", "-m", "n3x_bot"]
