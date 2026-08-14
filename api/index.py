import json, time, uuid, os
from flask import Flask, request, jsonify
import requests

# Base URL: overridable via env for test vs production.
# Test:    https://apitest.vipps.no
# Prod:    https://api.vipps.no
VIPPS_BASE = os.environ.get("VIPPS_BASE", "https://api.vipps.no")

# Where the user lands after approving (or cancelling) in the Vipps/MobilePay app.
# Reference is appended so the frontend can poll status for the right payment.
RETURN_URL = os.environ.get(
    "VIPPS_RETURN_URL",
    "https://matrise.enkelliv.no/?paymentRef=",
)

# Price in minor units (øre). 9900 = 99 kr.
PRICE_ORE = int(os.environ.get("VIPPS_PRICE_ORE", "9900"))
PAYMENT_DESCRIPTION = os.environ.get(
    "VIPPS_PAYMENT_DESCRIPTION",
    "Matrise - Selvinnsiktsquiz (99 kr)",
)

app = Flask(__name__)


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
    _token_cache["expires_at"] = now + int(data["expires_in"])
    return _token_cache["token"]


def _auth_headers(cfg, token):
    return {
        "Authorization": f"Bearer {token}",
        "Ocp-Apim-Subscription-Key": cfg["subscription_key"],
        "Merchant-Serial-Number": cfg["msn"],
        "Content-Type": "application/json",
        "Vipps-System-Name": "enkelliv",
        "Vipps-System-Version": "1.0.0",
    }


def _cors(resp):
    # matrise.enkelliv.no (GitHub Pages) calls this API cross-origin.
    origin = request.headers.get("Origin", "")
    allowed = {
        "https://enkelliv.no",
        "https://matrise.enkelliv.no",
        "https://vaaknopp.enkelliv.no",
        "http://localhost:3000",
        "http://localhost:8000",
    }
    if origin in allowed:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp


@app.after_request
def after_request(resp):
    return _cors(resp)


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "base": VIPPS_BASE, "price_ore": PRICE_ORE})


@app.route("/api/create-payment", methods=["POST", "OPTIONS"])
def create_payment():
    if request.method == "OPTIONS":
        return ("", 204)
    cfg = get_cfg()
    try:
        token = get_token(cfg)
        reference = f"matrise-{uuid.uuid4().hex[:12]}"
        idem = str(uuid.uuid4())
        payment_body = {
            "amount": {"currency": "NOK", "value": PRICE_ORE},
            "paymentMethod": {"type": "WALLET"},
            "reference": reference,
            "paymentDescription": PAYMENT_DESCRIPTION,
            "returnUrl": RETURN_URL + reference,
            "userFlow": "WEB_REDIRECT",
        }
        resp = requests.post(
            f"{VIPPS_BASE}/epayment/v1/payments",
            headers={**_auth_headers(cfg, token), "Idempotency-Key": idem},
            json=payment_body,
            timeout=15,
        )
        data = resp.json()
        if not resp.ok:
            return jsonify({"error": data}), resp.status_code
        return jsonify({
            "reference": data.get("reference"),
            "redirectUrl": data.get("redirectUrl"),
        }), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/payment-status", methods=["GET", "OPTIONS"])
def payment_status():
    if request.method == "OPTIONS":
        return ("", 204)
    cfg = get_cfg()
    reference = request.args.get("ref")
    if not reference:
        return jsonify({"error": "Missing 'ref' query parameter"}), 400
    try:
        token = get_token(cfg)
        resp = requests.get(
            f"{VIPPS_BASE}/epayment/v1/payments/{reference}",
            headers=_auth_headers(cfg, token),
            timeout=10,
        )
        data = resp.json()
        if not resp.ok:
            return jsonify({"error": data}), resp.status_code
        state = (data.get("aggregate") or {}).get("state", "UNKNOWN")
        # ePayment states: CREATED -> AUTHORIZED (approved) -> CAPTURED (money moved).
        # TERMINATED = cancelled/expired/rejected.
        paid = state in ("AUTHORIZED", "CAPTURED")
        return jsonify({
            "reference": data.get("reference"),
            "state": state,
            "amount": data.get("amount"),
            "paid": paid,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/capture-payment", methods=["POST", "OPTIONS"])
def capture_payment():
    """Capture a payment after approval. For digital delivery, capture immediately
    once the user has approved, so funds actually move and access can be granted."""
    if request.method == "OPTIONS":
        return ("", 204)
    cfg = get_cfg()
    body = request.get_json(silent=True) or {}
    reference = body.get("reference")
    if not reference:
        return jsonify({"error": "Missing 'reference' in body"}), 400
    try:
        token = get_token(cfg)
        idem = str(uuid.uuid4())
        resp = requests.post(
            f"{VIPPS_BASE}/epayment/v1/payments/{reference}/capture",
            headers={**_auth_headers(cfg, token), "Idempotency-Key": idem},
            json={"modificationAmount": {"currency": "NOK", "value": PRICE_ORE}},
            timeout=15,
        )
        data = resp.json()
        if not resp.ok:
            return jsonify({"error": data}), resp.status_code
        return jsonify({"captured": True, "reference": reference, "state": (data.get("aggregate") or {}).get("state")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/webhook", methods=["POST"])
def webhook():
    """Vipps sends payment status events here (e.g. AUTHORIZED, TERMINATED).
    For a static frontend we still poll /api/payment-status, but this endpoint
    gives us a server-side log of every state change for debugging and audit."""
    payload = request.get_json(silent=True) or {}
    reference = payload.get("reference") or payload.get("pspReference")
    state = (payload.get("aggregate") or {}).get("state", "UNKNOWN")
    # Log for audit; frontend still drives access via polling.
    print(f"[webhook] reference={reference} state={state}", flush=True)
    return jsonify({"received": True})


# Vercel serverless entrypoint
if __name__ == "__main__":
    app.run()
