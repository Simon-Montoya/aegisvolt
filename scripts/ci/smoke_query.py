"""Smoke test dentro del clúster: consulta el gateway local del pod (lee DynamoDB vía los servicios).
Uso: kubectl exec -i deploy/graphql-gateway-stable -- python - <env>   (el script llega por stdin)"""
import json
import sys
import urllib.request

env = sys.argv[1] if len(sys.argv) > 1 else None
query = "{ environment { name version services } stations { name availableConnectors } featureFlags { name enabled } }"
req = urllib.request.Request("http://127.0.0.1:8080/graphql", data=json.dumps({"query": query}).encode(), headers={"Content-Type": "application/json"})
body = json.load(urllib.request.urlopen(req, timeout=15))
print(json.dumps(body, indent=2, ensure_ascii=False))
assert not body.get("errors"), "GraphQL devolvio errores"
data = body["data"]
assert env is None or data["environment"]["name"] == env, "ambiente incorrecto"
assert all(v == "ok" for v in data["environment"]["services"].values()), "servicio caido"
assert sum(s["availableConnectors"] for s in data["stations"]) > 0, "sin estaciones"
print("SMOKE TEST OK")
