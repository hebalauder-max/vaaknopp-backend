import json, time, uuid, os
from flask import Flask, request, jsonify
import requests

VIPPS_BASE = "https://apitest.vipps.no"

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
    _token_cache["expires_at"] = now + data["expires_in"]
    return _token_cache["token"]


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/create-payment", methods=["POST"])
def create_payment():
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
            return jsonify({"error": data}), resp.status_code
        return jsonify({
            "reference": data.get("reference"),
            "redirectUrl": data.get("redirectUrl"),
        }), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/payment-status")
def payment_status():
    cfg = get_cfg()
    reference = request.args.get("ref")
    if not reference:
        return jsonify({"error": "Missing 'ref' query parameter"}), 400
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
            return jsonify({"error": data}), resp.status_code
        state = (data.get("aggregate") or {}).get("state", "UNKNOWN")
        paid = state in ("RESERVED", "CAPTURED", "SALE")
        return jsonify({
            "reference": data.get("reference"),
            "state": state,
            "amount": data.get("amount"),
            "paid": paid,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Vercel serverless entrypoint
if __name__ == "__main__":
    app.run()
