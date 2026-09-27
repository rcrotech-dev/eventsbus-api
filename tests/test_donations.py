import itertools

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.paypal import PayPalError, get_paypal

_ids = itertools.count(1)


class FakePayPal:
    def __init__(self):
        self.verify_result = True
        self.capture_status = "COMPLETED"

    def create_order(self, amount, currency, description, request_id):
        return {"id": f"ORDER{next(_ids)}", "status": "CREATED"}

    def capture_order(self, order_id, request_id):
        return {
            "id": order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {"payments": {"captures": [{"id": f"CAP{order_id}", "status": self.capture_status}]}}
            ],
        }

    def verify_webhook(self, headers, event):
        return self.verify_result


@pytest.fixture
def paypal():
    return FakePayPal()


@pytest.fixture
def client(paypal):
    app.dependency_overrides[get_paypal] = lambda: paypal
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def create(client, amount="25.00"):
    return client.post("/api/donations/orders", json={"amount": amount, "donor_name": "Ada"})


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_public_config_hides_secret(client):
    body = client.get("/api/config").json()
    assert body["paypal_client_id"] == "test-client"
    assert "secret" not in str(body).lower()


def test_create_and_capture(client):
    order = create(client).json()
    result = client.post(f"/api/donations/orders/{order['order_id']}/capture").json()
    assert result["status"] == "COMPLETED"
    assert result["capture_id"] == f"CAP{order['order_id']}"


@pytest.mark.parametrize("amount", ["0.50", "20000.00", "10.123", "-5"])
def test_rejects_invalid_amount(client, amount):
    assert create(client, amount).status_code == 422


def test_capture_unknown_order(client):
    assert client.post("/api/donations/orders/NOPE123/capture").status_code == 404


def test_capture_rejects_bad_order_id(client):
    assert client.post("/api/donations/orders/bad..id/capture").status_code == 422


def test_paypal_error_is_502(client, paypal):
    def boom(*a, **k):
        raise PayPalError(500, "internal")

    paypal.create_order = boom
    assert create(client).status_code == 502


def test_webhook_rejects_bad_signature(client, paypal):
    paypal.verify_result = False
    response = client.post("/api/paypal/webhook", json={"id": "WH-1", "event_type": "X"})
    assert response.status_code == 400


def test_webhook_completes_pending_donation(client, paypal):
    paypal.capture_status = "PENDING"
    order_id = create(client).json()["order_id"]
    assert client.post(f"/api/donations/orders/{order_id}/capture").json()["status"] == "PENDING"

    event = {
        "id": f"WH-{order_id}",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": f"CAP{order_id}",
            "status": "COMPLETED",
            "supplementary_data": {"related_ids": {"order_id": order_id}},
        },
    }
    assert client.post("/api/paypal/webhook", json=event).json() == {"status": "ok"}
    assert client.post("/api/paypal/webhook", json=event).json() == {"status": "duplicate"}
    again = client.post(f"/api/donations/orders/{order_id}/capture").json()
    assert again["status"] == "COMPLETED"


def test_webhook_refund(client):
    order_id = create(client).json()["order_id"]
    client.post(f"/api/donations/orders/{order_id}/capture")
    event = {
        "id": f"WH-REFUND-{order_id}",
        "event_type": "PAYMENT.CAPTURE.REFUNDED",
        "resource": {
            "id": "REFUND1",
            "links": [
                {"rel": "up", "href": f"https://api-m.sandbox.paypal.com/v2/payments/captures/CAP{order_id}"}
            ],
        },
    }
    assert client.post("/api/paypal/webhook", json=event).status_code == 200
