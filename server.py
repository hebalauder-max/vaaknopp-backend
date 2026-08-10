"""
Vaknopp Vipps ePayment backend (test environment).
Deploy to Render as Web Service (Python).
Set environment variables in Render dashboard:
  VIPPS_CLIENT_ID, VIPPS_CLIENT_SECRET, VIPPS_SUBSCRIPTION_KEY, VIPPS_MSN
"""

import json
import time
import uuid
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

import requests

# --- CONFIG (via environment variables) ---
VIPPS_BASE = "https://apitest.vipps.no"
CLIENT_ID = os.environ["VIPPS_CLIENT_ID"]
CLIENT_SECRET = os.environ["VIPPS_CLIENT_SECRET"]
SUBSCRIPTION_KEY = os.environ["VIPPS_SUBSCRIPTION_KEY"]
MSN = os.environ["VIPPS_MSN"]
RETURN_URL = "https://vaaknopp.enkelliv.no/"
PRICE_ORE = 9900  # 99 NOK
PORT = int(os.environ.get("PORT", 8000))

# --- Token cache ---
token_cache = {"token": None, "expires_at": 0}


def get_access_token():
    now = time.time()
    if token_cache["token"] and now < token_cache["expires_at"] - 60:
        return token_cache["token"]

    resp = requests.post(
        f"{VIPPS_BASE}/accessToken/get",
        headers={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    token_cache["token"] = data["access_token"]
    token_cache["expires_at"] = now + data["expires_in"]
    return token_cache["token"]


class VippsHandler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/health":
            self._json(200, {"status": "ok"})

        elif parsed.path.startswith("/api/payment/"):
            reference = parsed.path.split("/api/payment/")[1]
            if not reference:
                self._json(400, {"error": "Missing reference"})
                return
            try:
                token = get_access_token()
                resp = requests.get(
                    f"{VIPPS_BASE}/epayment/v1/payments/{reference}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
                        "Merchant-Serial-Number": MSN,
                    },
                    timeout=10,
                )
                data = resp.json()
                if not resp.ok:
                    self._json(resp.status_code, {"error": data})
                    return

                state = (data.get("aggregate") or {}).get("state", "UNKNOWN")
                paid = state in ("RESERVED", "CAPTURED", "SALE")

                self._json(200, {
                    "reference": data.get("reference"),
                    "state": state,
                    "amount": data.get("amount"),
                    "paid": paid,
                })
            except Exception as e:
                self._json(500, {"error": str(e)})

        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._json(400, {"error": "Invalid JSON"})
            return

        if parsed.path == "/api/create-payment":
            try:
                token = get_access_token()
                reference = f"vaaknopp-{uuid.uuid4().hex[:12]}"
                idem = str(uuid.uuid4())

                payment_body = {
                    "amount": {"value": PRICE_ORE, "currency": "NOK"},
                    "paymentMethod": {"type": "WALLET"},
                    "reference": reference,
                    "userFlow": "WEB_REDIRECT",
                    "returnUrl": RETURN_URL,
                    "paymentDescription": "Vaknopp - Full tilgang (99 kr)",
                }

                resp = requests.post(
                    f"{VIPPS_BASE}/epayment/v1/payments",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
                        "Merchant-Serial-Number": MSN,
                        "Content-Type": "application/json",
                        "Idempotency-Key": idem,
                    },
                    json=payment_body,
                    timeout=10,
                )
                data = resp.json()

                if not resp.ok:
                    self._json(resp.status_code, {"error": data})
                    return

                self._json(201, {
                    "reference": data.get("reference"),
                    "redirectUrl": data.get("redirectUrl"),
                })

            except Exception as e:
                self._json(500, {"error": str(e)})

        else:
            self._json(404, {"error": "Not found"})


if __name__ == "__main__":
    print(f"Vaknopp Vipps backend starting on port {PORT}")
    server = HTTPServer(("0.0.0.0", PORT), VippsHandler)
    server.serve_forever()
