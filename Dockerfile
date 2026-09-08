FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FASTSHOP_ENV=production \
    FASTSHOP_HOST=0.0.0.0 \
    FASTSHOP_PORT=5025 \
    FASTSHOP_AUTO_CREATE_SCHEMA=0

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system fastshop \
    && adduser --system --ingroup fastshop fastshop

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data \
    && chown -R fastshop:fastshop /app

USER fastshop

EXPOSE 5025
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl --fail http://127.0.0.1:5025/healthz || exit 1

CMD ["sh", "-c", "alembic upgrade head && python web_app.py"]

