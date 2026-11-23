"""billing-wallet-service: verificación de fondos, cobro y reembolso a la billetera de la flota.

Tabla: aegisvolt-billing-<env>  (pk, sk)
  WALLET#<fleet_id> / META                       -> saldo de la flota
  WALLET#<fleet_id> / LEDGER#<ts>#<saga>#<type>  -> libro mayor (append-only)
  CHARGE#<saga_id>  / META                       -> cobro (CHARGED | REFUNDED | REJECTED | VOIDED)

Paso 2 de la SAGA (cobrar) y su compensación (reembolsar). La clave de
idempotencia es el saga_id: el item CHARGE#<saga_id> se crea con
attribute_not_exists, así un cobro repetido nunca descuenta dos veces y un
reembolso repetido nunca devuelve dos veces (la condición status = CHARGED
solo se cumple una vez).
"""

from __future__ import annotations

import os
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError
from fastapi import FastAPI
from pydantic import BaseModel, Field

from aegis_common import aws, now_iso, to_jsonable
from aegis_common.config import Settings
from aegis_common.flags import FeatureFlags
from aegis_common.logs import configure_logging
from aegis_common.web import BusinessError, install_common

settings = Settings(service_name="billing-wallet-service")
log = configure_logging(settings)
flags = FeatureFlags(settings)
app = FastAPI(title="AegisVolt · billing-wallet-service", version=settings.app_version)
install_common(app, settings)

BASE_RATE_COP_PER_KWH = Decimal(os.getenv("BASE_RATE_COP_PER_KWH", "1100"))
ULTRA_RATE_MULTIPLIER = Decimal("1.25")  # >= 150 kW
SURGE_MULTIPLIER = Decimal(os.getenv("SURGE_MULTIPLIER", "1.5"))
PEAK_HOURS = {int(h) for h in os.getenv("PEAK_HOURS", "7,8,17,18,19,20").split(",") if h}
TIMEZONE = ZoneInfo(os.getenv("PRICING_TIMEZONE", "America/Bogota"))


def table():
    return aws.table(settings, "billing")


def wallet_key(fleet_id: str) -> dict:
    return {"pk": f"WALLET#{fleet_id}", "sk": "META"}


def charge_key(saga_id: str) -> dict:
    return {"pk": f"CHARGE#{saga_id}", "sk": "META"}


def is_peak(moment: datetime | None = None) -> bool:
    if os.getenv("SURGE_FORCE_PEAK", "false").lower() == "true":
        return True
    moment = moment or datetime.now(TIMEZONE)
    return moment.astimezone(TIMEZONE).hour in PEAK_HOURS


def quote(energy_kwh: Decimal, power_kw: int, surge_enabled: bool, peak: bool) -> dict:
    """Precio estimado de la sesión. Función pura: se prueba en tests/unit."""
    rate = BASE_RATE_COP_PER_KWH * (ULTRA_RATE_MULTIPLIER if power_kw >= 150 else Decimal("1"))
    surge = SURGE_MULTIPLIER if (surge_enabled and peak) else Decimal("1")
    amount = (energy_kwh * rate * surge).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return {"rate_cop_per_kwh": rate, "surge_multiplier": surge, "surge_applied": surge > 1, "amount": amount}


class ChargeRequest(BaseModel):
    saga_id: str = Field(min_length=8)
    fleet_id: str
    energy_kwh: Decimal = Field(gt=0, le=500)
    power_kw: int = Field(ge=1, le=1000)


class WalletRequest(BaseModel):
    fleet_id: str
    name: str
    balance: Decimal = Field(ge=0)


def _get(key: dict) -> dict | None:
    return table().get_item(Key=key, ConsistentRead=True).get("Item")


def _ledger_item(fleet_id: str, saga_id: str, kind: str, amount: Decimal, balance_hint: str) -> dict:
    ts = now_iso()
    return {
        "pk": f"WALLET#{fleet_id}",
        "sk": f"LEDGER#{ts}#{saga_id}#{kind}",
        "saga_id": saga_id,
        "type": kind,
        "amount": amount,
        "note": balance_hint,
        "created_at": ts,
    }


@app.post("/wallets", status_code=201)
def create_wallet(req: WalletRequest):
    try:
        table().put_item(
            Item={**wallet_key(req.fleet_id), "fleet_id": req.fleet_id, "name": req.name, "balance": req.balance, "currency": "COP", "created_at": now_iso()},
            ConditionExpression="attribute_not_exists(pk)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        raise BusinessError(409, "WALLET_EXISTS", "La billetera ya existe") from exc
    return to_jsonable(_get(wallet_key(req.fleet_id)))


@app.get("/wallets/{fleet_id}")
def get_wallet(fleet_id: str):
    wallet = _get(wallet_key(fleet_id))
    if not wallet:
        raise BusinessError(404, "WALLET_NOT_FOUND", "No existe la billetera")
    ledger = table().query(
        KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
        ExpressionAttributeValues={":pk": f"WALLET#{fleet_id}", ":prefix": "LEDGER#"},
        ScanIndexForward=False,
        Limit=20,
    ).get("Items", [])
    return to_jsonable({**wallet, "ledger": ledger})


@app.get("/charges/{saga_id}")
def get_charge(saga_id: str):
    item = _get(charge_key(saga_id))
    if not item:
        raise BusinessError(404, "CHARGE_NOT_FOUND", "No existe el cobro")
    return to_jsonable(item)


@app.post("/charges", status_code=201)
def charge(req: ChargeRequest):
    """Paso 2 · descuenta el saldo estimado. Fondos insuficientes -> 402 (dispara compensación del paso 1)."""
    existing = _get(charge_key(req.saga_id))
    if existing:
        return _replay(existing)

    wallet = _get(wallet_key(req.fleet_id))
    if not wallet:
        raise BusinessError(404, "WALLET_NOT_FOUND", f"No existe la billetera {req.fleet_id}")

    surge_enabled = flags.is_enabled("ENABLE_DYNAMIC_SURGE_PRICING")
    price = quote(req.energy_kwh, req.power_kw, surge_enabled, is_peak())
    amount = price["amount"]
    now = now_iso()
    charge_item = {
        **charge_key(req.saga_id),
        "saga_id": req.saga_id,
        "fleet_id": req.fleet_id,
        "energy_kwh": req.energy_kwh,
        "amount": amount,
        "rate_cop_per_kwh": price["rate_cop_per_kwh"],
        "surge_multiplier": price["surge_multiplier"],
        "surge_applied": price["surge_applied"],
        "status": "CHARGED",
        "created_at": now,
        "updated_at": now,
    }
    billing_table = settings.table_name("billing")
    try:
        aws.dynamodb(settings).meta.client.transact_write_items(
            TransactItems=[
                {"Put": {"TableName": billing_table, "Item": charge_item, "ConditionExpression": "attribute_not_exists(pk)"}},
                {
                    "Update": {
                        "TableName": billing_table,
                        "Key": wallet_key(req.fleet_id),
                        "UpdateExpression": "SET balance = balance - :amount, updated_at = :now",
                        "ConditionExpression": "balance >= :amount",
                        "ExpressionAttributeValues": {":amount": amount, ":now": now},
                    }
                },
                {"Put": {"TableName": billing_table, "Item": _ledger_item(req.fleet_id, req.saga_id, "DEBIT", amount, "charge")}},
            ]
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        reasons = [r.get("Code", "") for r in exc.response.get("CancellationReasons", [])]
        if reasons and reasons[0] == "ConditionalCheckFailed":
            return _replay(_get(charge_key(req.saga_id)))
        # Fondos insuficientes: se registra el rechazo para que un reintento responda igual.
        rejected = {**charge_item, "status": "REJECTED", "reason": "INSUFFICIENT_FUNDS", "balance_at_attempt": wallet["balance"]}
        try:
            table().put_item(Item=rejected, ConditionExpression="attribute_not_exists(pk)")
        except ClientError:
            pass
        log.warning("charge_rejected_insufficient_funds", extra={"fleet_id": req.fleet_id, "amount": str(amount), "balance": str(wallet["balance"])})
        raise BusinessError(402, "INSUFFICIENT_FUNDS", "Saldo insuficiente en la billetera de la flota", amount=float(amount), balance=float(wallet["balance"])) from exc

    log.info("wallet_charged", extra={"fleet_id": req.fleet_id, "amount": str(amount), "surge_applied": price["surge_applied"]})
    return to_jsonable({**charge_item, "idempotent": False})


def _replay(existing: dict):
    status = existing["status"]
    if status == "REJECTED":
        raise BusinessError(402, "INSUFFICIENT_FUNDS", "Saldo insuficiente (respuesta repetida)", idempotent=True)
    if status in ("VOIDED", "REFUNDED"):
        raise BusinessError(409, "CHARGE_ALREADY_COMPENSATED", "El cobro de esta SAGA ya fue compensado", idempotent=True)
    log.info("charge_idempotent_replay")
    return to_jsonable({**existing, "idempotent": True})


@app.post("/charges/{saga_id}/refund")
def refund(saga_id: str, reason: str = "SAGA_COMPENSATION"):
    """Compensación del paso 2 · CHARGED -> REFUNDED y saldo += monto (idempotente)."""
    existing = _get(charge_key(saga_id))
    now = now_iso()
    if existing is None:
        # Nunca se cobró (p. ej. timeout antes de llegar). Lápida VOIDED: un cobro tardío será rechazado.
        try:
            table().put_item(
                Item={**charge_key(saga_id), "saga_id": saga_id, "status": "VOIDED", "reason": reason, "created_at": now, "updated_at": now},
                ConditionExpression="attribute_not_exists(pk)",
            )
            log.info("charge_voided", extra={"reason": reason})
            return {"saga_id": saga_id, "status": "VOIDED", "idempotent": False}
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            existing = _get(charge_key(saga_id))

    if existing["status"] in ("REFUNDED", "VOIDED", "REJECTED"):
        log.info("refund_idempotent_replay", extra={"status": existing["status"]})
        return to_jsonable({**existing, "idempotent": True})

    amount = existing["amount"]
    billing_table = settings.table_name("billing")
    try:
        aws.dynamodb(settings).meta.client.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": billing_table,
                        "Key": charge_key(saga_id),
                        "UpdateExpression": "SET #s = :refunded, refunded_at = :now, updated_at = :now, refund_reason = :reason",
                        "ConditionExpression": "#s = :charged",
                        "ExpressionAttributeNames": {"#s": "status"},
                        "ExpressionAttributeValues": {":refunded": "REFUNDED", ":charged": "CHARGED", ":now": now, ":reason": reason},
                    }
                },
                {
                    "Update": {
                        "TableName": billing_table,
                        "Key": wallet_key(existing["fleet_id"]),
                        "UpdateExpression": "SET balance = balance + :amount, updated_at = :now",
                        "ExpressionAttributeValues": {":amount": amount, ":now": now},
                    }
                },
                {"Put": {"TableName": billing_table, "Item": _ledger_item(existing["fleet_id"], saga_id, "REFUND", amount, reason)}},
            ]
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        current = _get(charge_key(saga_id))
        if current and current["status"] == "REFUNDED":
            return to_jsonable({**current, "idempotent": True})
        raise BusinessError(409, "REFUND_CONFLICT", "No se pudo reembolsar") from exc

    log.info("wallet_refunded", extra={"fleet_id": existing["fleet_id"], "amount": str(amount), "reason": reason})
    return to_jsonable({**existing, "status": "REFUNDED", "idempotent": False})
