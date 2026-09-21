# Python version matches CI. If one goes up, raise both.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

# Runtime dependencies only. requirements-dev.txt does not go into the image.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

# Do not run as root.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /srv
USER appuser

EXPOSE 8000

# Migrations deliberately do not run here. See the README.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
