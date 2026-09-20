# Versi Python disamakan dengan CI. Kalau salah satu naik, naikkan keduanya.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

# Hanya dependency runtime. requirements-dev.txt tidak ikut ke image.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

# Jangan jalan sebagai root.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /srv
USER appuser

EXPOSE 8000

# Migration sengaja tidak dijalankan di sini. Lihat README.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
