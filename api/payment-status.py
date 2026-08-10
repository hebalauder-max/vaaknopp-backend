import json, time, os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
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
        # Read reference from query param: ?ref=xxx
        qs = urlparse(self.path).query
        params = parse_qs(qs)
        reference = params.get("ref", [None])[0]

        if not reference:
            self._json(400, {"error": "Missing 'ref' query parameter"})
            return

        try:
            token = get_token(cfg)
            resp = requests.get(
                f"{VIPPS_BASE}/epayment/v1/payments/{reference}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Ocp-Apim-Subscription-Key": cfg["subscription_key"],
                    "Merchant-Serial-Number": cfg["msn"],
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

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
