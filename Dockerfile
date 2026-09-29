FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    ECOVRI_DATA_DIR=/var/lib/ecovri

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY wsgi.py .

RUN useradd --create-home --uid 10001 ecovri \
    && mkdir -p /var/lib/ecovri \
    && chown -R ecovri:ecovri /app /var/lib/ecovri

USER ecovri
EXPOSE 8000

CMD ["gunicorn", "--workers", "2", "--timeout", "120", "--access-logfile", "-", "--bind", "0.0.0.0:8000", "wsgi:app"]
