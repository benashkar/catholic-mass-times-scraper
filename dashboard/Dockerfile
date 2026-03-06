# Dockerfile for Catholic Church Dashboard
# Build context should be the REPO ROOT (not dashboard/)
# Render sets rootDir in render.yaml but Docker needs repo root for data access

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY dashboard/requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

# Copy dashboard application code
COPY dashboard/app/ /app/app/

# Copy ONLY the CSV data files we need (not raw PDFs, texts, etc.)
# Each state folder has: parsed_addresses.csv, all_services.csv, bulletin_names.csv
COPY data/output/ /app/data/output/

# Environment
ENV FLASK_APP=app
ENV DATA_DIR=/app/data/output
ENV SECRET_KEY=change-me-in-production

EXPOSE 10000

CMD ["gunicorn", "--bind", "0.0.0.0:10000", "--workers", "2", "--timeout", "120", "app:create_app()"]
