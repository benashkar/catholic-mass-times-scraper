# Dockerfile for Catholic Church Dashboard
# Reads all data from db99 MySQL (no CSV files needed)

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY dashboard/requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

# Copy dashboard application code
COPY dashboard/app/ /app/app/

# /health imports src.utils.health_checks, which was absent from this image and
# made the endpoint 500. Copy ONLY those three files, not all of src/: the rest
# pulls scraper dependencies (spacy, playwright, pdfplumber) the dashboard does
# not need. health_checks.py imports nothing outside the stdlib and both
# __init__.py files are docstring-only, so this stays slim.
COPY src/__init__.py /app/src/__init__.py
COPY src/utils/__init__.py /app/src/utils/__init__.py
COPY src/utils/health_checks.py /app/src/utils/health_checks.py

# Environment
ENV FLASK_APP=app
ENV SECRET_KEY=change-me-in-production

EXPOSE 10000

CMD ["gunicorn", "--bind", "0.0.0.0:10000", "--workers", "2", "--timeout", "120", "app:create_app()"]
