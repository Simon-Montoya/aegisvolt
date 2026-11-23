# Imagen de graphql-gateway. Contexto de build: raíz del repositorio
#   docker build -f services/graphql-gateway/Dockerfile -t aegisvolt/graphql-gateway:<tag> .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/libs:/app

WORKDIR /app
COPY services/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt
COPY services/graphql-gateway/requirements.txt /tmp/req-gateway.txt
RUN pip install --no-cache-dir -r /tmp/req-gateway.txt

COPY libs/ /app/libs/
COPY services/graphql-gateway/graphql_gateway/ /app/graphql_gateway/

ARG APP_VERSION=local
ENV APP_VERSION=${APP_VERSION}

RUN useradd --uid 10001 --create-home aegis
USER 10001

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"
CMD ["uvicorn", "graphql_gateway.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
