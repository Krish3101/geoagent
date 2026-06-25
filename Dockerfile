FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y gcc g++ libpq-dev libgdal-dev curl && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev

COPY frontend /app/frontend
COPY src /app/src

EXPOSE 8000
CMD ["uv", "run", "python", "-m", "src.main"]
