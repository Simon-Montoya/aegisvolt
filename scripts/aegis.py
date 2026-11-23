#!/usr/bin/env python3
"""CLI de operación de AegisVolt contra Floci (o AWS).

  python scripts/aegis.py seed  --env dev
  python scripts/aegis.py flags --env dev                          # ver flags
  python scripts/aegis.py flags --env test --set SIMULATE_CHARGER_HARDWARE_FAULT=true
  python scripts/aegis.py sagas --env test                         # últimas SAGAs
  python scripts/aegis.py trace --env test --saga <saga_id>        # recorrido en CloudWatch
  python scripts/aegis.py inspect --env test --saga <saga_id>      # estado en las 3 tablas
  python scripts/aegis.py tfstate --env dev                        # versiones del tfstate en S3
  python scripts/aegis.py tfstate --env dev --restore <version_id> # restaurar una versión

Endpoint: AWS_ENDPOINT_URL (por defecto http://localhost:4566).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs"))
os.environ.setdefault("AWS_ENDPOINT_URL", "http://localhost:4566")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from aegis_common import aws, to_jsonable  # noqa: E402
from aegis_common.config import Settings  # noqa: E402
from aegis_common.flags import FeatureFlags  # noqa: E402
from aegis_common.seed import seed  # noqa: E402


def out(data) -> None:
    print(json.dumps(to_jsonable(data), indent=2, ensure_ascii=False, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["seed", "flags", "sagas", "trace", "inspect", "tfstate"])
    parser.add_argument("--env", required=True, choices=["dev", "test", "prod"])
    parser.add_argument("--set", help="NOMBRE=true|false")
    parser.add_argument("--saga")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--restore", metavar="VERSION_ID")
    parser.add_argument("--download", metavar="VERSION_ID")
    args = parser.parse_args()

    settings = Settings(service_name="aegis-cli", environment=args.env)
    dynamodb = aws.dynamodb(settings)

    if args.command == "seed":
        out(seed(dynamodb, settings, include_flags=True))

    elif args.command == "flags":
        flags = FeatureFlags(settings)
        if args.set:
            name, value = args.set.split("=", 1)
            out(flags.set(name, value.lower() == "true", actor=f"cli:{os.getenv('USERNAME', os.getenv('USER', 'operator'))}"))
        else:
            out(flags.all())

    elif args.command == "sagas":
        items = dynamodb.Table(settings.table_name("sagas")).scan().get("Items", [])
        items.sort(key=lambda s: s.get("created_at", ""), reverse=True)
        out([{k: s.get(k) for k in ("saga_id", "status", "failure_reason", "fleet_id", "connector_id", "amount", "created_at")} for s in items[: args.limit]])

    elif args.command == "inspect":
        if not args.saga:
            parser.error("--saga es obligatorio")
        sid = args.saga
        out(
            {
                "saga": dynamodb.Table(settings.table_name("sagas")).get_item(Key={"saga_id": sid}).get("Item"),
                "reservation": dynamodb.Table(settings.table_name("reservations")).get_item(Key={"pk": f"RESERVATION#{sid}", "sk": "META"}).get("Item"),
                "charge": dynamodb.Table(settings.table_name("billing")).get_item(Key={"pk": f"CHARGE#{sid}", "sk": "META"}).get("Item"),
                "session": dynamodb.Table(settings.table_name("dispatch")).get_item(Key={"pk": f"SESSION#{sid}", "sk": "META"}).get("Item"),
            }
        )

    elif args.command == "tfstate":
        s3 = aws.client(settings, "s3")
        bucket, key = "aegisvolt-tfstate", f"env:/{args.env}/aegisvolt/terraform.tfstate"
        if args.restore:
            # Copiar la versión vieja ENCIMA de la actual: la corrupta queda en el historial.
            s3.copy_object(Bucket=bucket, Key=key, CopySource={"Bucket": bucket, "Key": key, "VersionId": args.restore})
            print(f"Restaurado {key} a la versión {args.restore}. Verifique: terraform workspace select {args.env}; terraform plan -var-file=env/{args.env}.tfvars")
        elif args.download:
            body = s3.get_object(Bucket=bucket, Key=key, VersionId=args.download)["Body"].read()
            path = f"tfstate-{args.env}-{args.download}.json"
            Path(path).write_bytes(body)
            print(f"Guardado en {path} (serial {json.loads(body).get('serial')})")
        else:
            print("Versionamiento del bucket:", s3.get_bucket_versioning(Bucket=bucket).get("Status"))
            versions = s3.list_object_versions(Bucket=bucket, Prefix=key).get("Versions", [])
            for v in sorted(versions, key=lambda v: v["LastModified"], reverse=True):
                print(f"{'* latest' if v['IsLatest'] else '        '}  {v['LastModified']:%Y-%m-%d %H:%M:%S}  {v['Size']:>8} B  {v['VersionId']}")

    elif args.command == "trace":
        if not args.saga:
            parser.error("--saga es obligatorio")
        logs = aws.client(settings, "logs")
        events = []
        prefix = f"/aegisvolt/{args.env}/"
        for group in logs.describe_log_groups(logGroupNamePrefix=prefix).get("logGroups", []):
            response = logs.filter_log_events(logGroupName=group["logGroupName"], filterPattern=f'{{ $.correlation_id = "{args.saga}" }}')
            for event in response.get("events", []):
                try:
                    events.append(json.loads(event["message"]))
                except ValueError:
                    events.append({"raw": event["message"]})
        events.sort(key=lambda e: e.get("timestamp", ""))
        for e in events:
            print(f"{e.get('timestamp', '')[11:23]}  {e.get('service', ''):28} {e.get('level', ''):7} {e.get('message', '')}  {e.get('detail') or e.get('path') or ''}")
        print(f"\n{len(events)} eventos con correlation_id={args.saga}")


if __name__ == "__main__":
    main()
