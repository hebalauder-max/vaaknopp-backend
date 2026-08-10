import json, time, uuid, os
from http.server import BaseHTTPRequestHandler
import requests

VIPPS_BASE = "https://apitest.vipps.no"

def get_cfg():
    return {
        "client_id": os.environ["VIPPS_CLIENT_ID"],
        "client_secret": os.environ["VIPPS_CLIENT_SECRET"],
        "subscription_key": os.environ["VIPPS_SUBSCRIPTION_KEY"],
        "msn": os.environ["VIPPS_MSN"],
    }

_token_cache = {"token": None, "expires_at": 0}

def get_token(cfg):
    global _token_cache
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    resp = requests.post(
        f"{VIPPS_BASE}/accessToken/get",
        headers={
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "Ocp-Apim-Subscription-Key": cfg["subscription_key"],
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data["expires_in"]
    return _token_cache["token"]


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        cfg = get_cfg()
        self._json(200, {"status": "ok"})

    def do_POST(self):
        cfg = get_cfg()
        try:
            token = get_token(cfg)
            reference = f"vaaknopp-{uuid.uuid4().hex[:12]}"
            idem = str(uuid.uuid4())
            payment_body = {
                "amount": {"value": 9900, "currency": "NOK"},
                "paymentMethod": {"type": "WALLET"},
                "reference": reference,
                "userFlow": "WEB_REDIRECT",
                "returnUrl": "https://vaaknopp.enkelliv.no/",
                "paymentDescription": "Vaknopp - Full tilgang (99 kr)",
            }
            resp = requests.post(
                f"{VIPPS_BASE}/epayment/v1/payments",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Ocp-Apim-Subscription-Key": cfg["subscription_key"],
                    "Merchant-Serial-Number": cfg["msn"],
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
            self._json(201, {"reference": data.get("reference"), "redirectUrl": data.get("redirectUrl")})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
