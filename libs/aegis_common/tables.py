"""Definición de las tablas DynamoDB para pruebas locales y CI.

En los ambientes dev/test/prod la fuente de verdad es Terraform
(infra/modules/dynamodb). Este módulo crea las MISMAS tablas en moto o en un
Floci efímero del pipeline para correr las pruebas de integración y e2e.
"""

from __future__ import annotations

from .config import Settings

TABLES = {
    "reservations": [("pk", "S"), ("sk", "S")],
    "billing": [("pk", "S"), ("sk", "S")],
    "dispatch": [("pk", "S"), ("sk", "S")],
    "sagas": [("saga_id", "S")],
    "feature-flags": [("name", "S")],
}


def create_all(dynamodb, settings: Settings) -> list[str]:
    created = []
    existing = set(dynamodb.meta.client.list_tables().get("TableNames", []))
    for kind, keys in TABLES.items():
        name = settings.table_name(kind)
        if name in existing:
            continue
        schema = [{"AttributeName": keys[0][0], "KeyType": "HASH"}]
        if len(keys) > 1:
            schema.append({"AttributeName": keys[1][0], "KeyType": "RANGE"})
        dynamodb.create_table(
            TableName=name,
            KeySchema=schema,
            AttributeDefinitions=[{"AttributeName": k, "AttributeType": t} for k, t in keys],
            BillingMode="PAY_PER_REQUEST",
        )
        created.append(name)
    for name in created:
        dynamodb.meta.client.get_waiter("table_exists").wait(TableName=name)
    return created


def delete_all(dynamodb, settings: Settings) -> None:
    existing = set(dynamodb.meta.client.list_tables().get("TableNames", []))
    for kind in TABLES:
        name = settings.table_name(kind)
        if name in existing:
            dynamodb.Table(name).delete()
