# Imagen de billing-wallet. Contexto de build: raíz del repositorio
#   docker build -f services/billing-wallet/Dockerfile -t aegisvolt/billing-wallet:<tag> .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/libs:/app

WORKDIR /app
COPY services/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt

COPY libs/ /app/libs/
COPY services/billing-wallet/billing_wallet/ /app/billing_wallet/

ARG APP_VERSION=local
ENV APP_VERSION=${APP_VERSION}

RUN useradd --uid 10001 --create-home aegis
USER 10001

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"
CMD ["uvicorn", "billing_wallet.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
