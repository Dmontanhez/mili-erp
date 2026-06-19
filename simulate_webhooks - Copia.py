import hashlib
import hmac
import json
import random
import time
import uuid
from datetime import datetime, timezone

import httpx

IUGU_SECRET = "test_secret_iugu"
IUGU_URL = "http://localhost:8000/webhooks/iugu"
MELI_URL = "http://localhost:8000/webhooks/meli"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def iugu_signature(payload: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def send_iugu_webhook(event_type: str, data: dict) -> None:
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    signature = iugu_signature(payload, IUGU_SECRET)
    headers = {
        "Content-Type": "application/json",
        "X-Iugu-Signature": signature,
    }
    with httpx.Client() as client:
        response = client.post(IUGU_URL, content=payload.encode("utf-8"), headers=headers)
    print(f"[IUGU {event_type}] status={response.status_code} body={response.text}")


def simulate_iugu_invoice_paid() -> None:
    invoice_id = f"invoice_{uuid.uuid4().hex[:12]}"
    subscription_id = f"sub_{uuid.uuid4().hex[:12]}"
    data = {
        "event": "invoice.status.changed",
        "data": {
            "id": invoice_id,
            "status": "paid",
            "subscription_id": subscription_id,
            "cycle": "monthly",
            "paid_at": now_iso(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "amount": random.randint(1000, 50000),
            "currency": "BRL",
        },
    }
    send_iugu_webhook("invoice.status.changed", data)


def simulate_iugu_subscription_canceled() -> None:
    subscription_id = f"sub_{uuid.uuid4().hex[:12]}"
    data = {
        "event": "subscription.canceled",
        "data": {
            "id": subscription_id,
            "status": "canceled",
            "canceled_at": now_iso(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "plan_identifier": "plan_basic",
            "customer_email": "cliente@exemplo.com",
            "customer_name": "Cliente Exemplo",
        },
    }
    send_iugu_webhook("subscription.canceled", data)


def simulate_iugu_payment_failed() -> None:
    invoice_id = f"invoice_{uuid.uuid4().hex[:12]}"
    subscription_id = f"sub_{uuid.uuid4().hex[:12]}"
    data = {
        "event": "invoice.payment_failed",
        "data": {
            "id": invoice_id,
            "status": "pending",
            "subscription_id": subscription_id,
            "errors": ["cartao recusado"],
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "amount": random.randint(1000, 50000),
            "currency": "BRL",
        },
    }
    send_iugu_webhook("invoice.payment_failed", data)


def send_meli_webhook(topic: str, resource: str, data: dict) -> None:
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    headers = {
        "Content-Type": "application/json",
        "X-Topic": topic,
        "X-Resource": resource,
    }
    with httpx.Client() as client:
        response = client.post(MELI_URL, content=payload.encode("utf-8"), headers=headers)
    print(f"[MELI {topic}] status={response.status_code} body={response.text}")


def simulate_meli_payment() -> None:
    payment_id = random.randint(1000000000, 9999999999)
    data = {
        "id": payment_id,
        "status": "approved",
        "status_detail": "accredited",
        "transaction_amount": round(random.uniform(10.0, 500.0), 2),
        "currency_id": "BRL",
        "date_created": now_iso(),
        "date_approved": now_iso(),
        "payer": {
            "id": random.randint(100000, 999999),
            "email": "comprador@exemplo.com",
        },
        "external_reference": f"order_{uuid.uuid4().hex[:12]}",
    }
    send_meli_webhook("payment", f"/v1/payments/{payment_id}", data)


def simulate_meli_subscription_authorized() -> None:
    preapproval_id = f"preapproval_{uuid.uuid4().hex[:12]}"
    data = {
        "id": preapproval_id,
        "status": "authorized",
        "payer_id": random.randint(100000, 999999),
        "payer_email": "assinante@exemplo.com",
        "external_reference": f"sub_{uuid.uuid4().hex[:12]}",
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": round(random.uniform(10.0, 200.0), 2),
            "currency_id": "BRL",
        },
        "date_created": now_iso(),
        "last_modified": now_iso(),
    }
    send_meli_webhook("subscription_authorized", f"/v1/preapproval/{preapproval_id}", data)


def simulate_meli_subscription_cancelled() -> None:
    preapproval_id = f"preapproval_{uuid.uuid4().hex[:12]}"
    data = {
        "id": preapproval_id,
        "status": "cancelled",
        "payer_id": random.randint(100000, 999999),
        "payer_email": "assinante@exemplo.com",
        "external_reference": f"sub_{uuid.uuid4().hex[:12]}",
        "date_created": now_iso(),
        "last_modified": now_iso(),
    }
    send_meli_webhook("subscription_cancelled", f"/v1/preapproval/{preapproval_id}", data)


def main() -> None:
    print("Iniciando simulação de webhooks...")
    print(f"Timestamp base: {datetime.now(timezone.utc).isoformat()}")

    print("\n--- IUGU ---")
    simulate_iugu_invoice_paid()
    time.sleep(0.2)
    simulate_iugu_payment_failed()
    time.sleep(0.2)
    simulate_iugu_subscription_canceled()

    print("\n--- Mercado Pago ---")
    simulate_meli_payment()
    time.sleep(0.2)
    simulate_meli_subscription_authorized()
    time.sleep(0.2)
    simulate_meli_subscription_cancelled()

    print("\nSimulação finalizada.")


if __name__ == "__main__":
    main()