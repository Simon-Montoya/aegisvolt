"""Smoke test dentro del clúster: consulta el gateway local del pod (lee DynamoDB vía los servicios).

Uso: kubectl exec -i deploy/graphql-gateway-stable -- python - <env>   (el script llega por stdin)

Reintenta hasta ~90 s: justo después de un despliegue los pods nuevos abren su primera conexión
a DynamoDB y la primera consulta puede tardar más de lo normal.
"""
import json
import sys
import time
import urllib.request

env = sys.argv[1] if len(sys.argv) > 1 else None
query = "{ environment { name version services } stations { name availableConnectors } featureFlags { name enabled } }"
ATTEMPTS, PAUSE = 10, 8


def check():
    req = urllib.request.Request("http://127.0.0.1:8080/graphql", data=json.dumps({"query": query}).encode(), headers={"Content-Type": "application/json"})
    body = json.load(urllib.request.urlopen(req, timeout=20))
    if body.get("errors"):
        raise RuntimeError("GraphQL devolvio errores: " + "; ".join(e.get("message", "") for e in body["errors"]))
    data = body["data"]
    if env and data["environment"]["name"] != env:
        raise RuntimeError(f"ambiente incorrecto: {data['environment']['name']}")
    caidos = [k for k, v in data["environment"]["services"].items() if v != "ok"]
    if caidos:
        raise RuntimeError(f"servicios caidos: {caidos}")
    if sum(s["availableConnectors"] for s in data["stations"]) <= 0:
        raise RuntimeError("sin estaciones disponibles")
    return body


for attempt in range(1, ATTEMPTS + 1):
    try:
        result = check()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"SMOKE TEST OK (intento {attempt})")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"intento {attempt}/{ATTEMPTS}: {exc}", flush=True)
        if attempt < ATTEMPTS:
            time.sleep(PAUSE)
sys.exit("SMOKE TEST FALLIDO")
