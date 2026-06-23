FROM python:3.13-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md ./
COPY adp ./adp
COPY eval ./eval
RUN pip install --no-cache-dir .

EXPOSE 8000
# scale-to-zero friendly; bind all interfaces for container/cloud runtimes.
CMD ["uvicorn", "adp.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
