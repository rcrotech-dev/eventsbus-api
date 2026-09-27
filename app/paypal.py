import logging
import threading
import time
from collections.abc import Mapping
from decimal import Decimal
from functools import lru_cache
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

WEBHOOK_HEADERS = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}


class PayPalError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"PayPal API error {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class PayPalClient:
    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        webhook_id: str,
        http: httpx.Client | None = None,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._webhook_id = webhook_id
        self._http = http or httpx.Client(base_url=base_url, timeout=15.0)
        self._token: str | None = None
        self._token_expiry = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if response.is_error:
            raise PayPalError(response.status_code, response.text)

    def _access_token(self) -> str:
        with self._lock:
            if self._token and time.monotonic() < self._token_expiry - 60:
                return self._token
            response = self._http.post(
                "/v1/oauth2/token",
                data={"grant_type": "client_credentials"},
                auth=(self._client_id, self._client_secret),
            )
            self._check(response)
            body = response.json()
            self._token = body["access_token"]
            self._token_expiry = time.monotonic() + int(body["expires_in"])
            return self._token

    def _request(self, method: str, path: str, headers: dict | None = None, **kwargs) -> dict:
        all_headers = {"Authorization": f"Bearer {self._access_token()}", **(headers or {})}
        response = self._http.request(method, path, headers=all_headers, **kwargs)
        self._check(response)
        return response.json()

    def create_order(
        self, amount: Decimal, currency: str, description: str, request_id: str
    ) -> dict[str, Any]:
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "description": description,
                    "amount": {
                        "currency_code": currency,
                        "value": str(amount.quantize(Decimal("0.01"))),
                    },
                }
            ],
        }
        return self._request(
            "POST", "/v2/checkout/orders", json=payload, headers={"PayPal-Request-Id": request_id}
        )

    def capture_order(self, order_id: str, request_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v2/checkout/orders/{order_id}/capture",
            json={},
            headers={"PayPal-Request-Id": request_id},
        )

    def verify_webhook(self, headers: Mapping[str, str], event: dict[str, Any]) -> bool:
        if not self._webhook_id:
            log.error("PAYPAL_WEBHOOK_ID is not configured; rejecting webhook")
            return False
        values = {key: headers.get(header) for key, header in WEBHOOK_HEADERS.items()}
        if not all(values.values()):
            return False
        body = {**values, "webhook_id": self._webhook_id, "webhook_event": event}
        result = self._request("POST", "/v1/notifications/verify-webhook-signature", json=body)
        return result.get("verification_status") == "SUCCESS"


@lru_cache
def get_paypal() -> PayPalClient:
    s = get_settings()
    return PayPalClient(
        s.paypal_base_url, s.paypal_client_id, s.paypal_client_secret, s.paypal_webhook_id
    )
