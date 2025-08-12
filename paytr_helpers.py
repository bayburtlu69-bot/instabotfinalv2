# paytr_helpers.py
# -*- coding: utf-8 -*-
"""
Drop-in PayTR helpers.
Usage:
    1) Put this file next to your panel.py
    2) In panel.py add:
        import base64, hashlib, hmac, json
        from decimal import Decimal
        from paytr_helpers import client_ip, build_user_basket, paytr_token
    3) In /bakiye-yukle POST handler:
        - Use build_user_basket("Bakiye Yükleme", 1, amount_tl) to create user_basket
        - Create 'data' dict then set data["paytr_token"] = paytr_token(data, MERCHANT_KEY, MERCHANT_SALT)
    4) Send POST to https://www.paytr.com/odeme/api/get-token
"""

import base64
import json
import hashlib
import hmac

def client_ip(request):
    """Return client IP, considering common reverse-proxy headers."""
    return (
        request.headers.get("CF-Connecting-IP")
        or (request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or None)
        or request.remote_addr
        or "127.0.0.1"
    )

def build_user_basket(title, qty, price_tl):
    """
    Build PayTR user_basket: Base64(JSON).
    Example JSON -> [["Bakiye Yükleme", 1, "100.00"]]
    """
    try:
        from decimal import Decimal
        price_str = f"{Decimal(price_tl):.2f}"
    except Exception:
        price_str = "{:.2f}".format(float(price_tl))
    basket = [[title, int(qty), price_str]]
    js = json.dumps(basket, separators=(",", ":"))
    return base64.b64encode(js.encode("utf-8")).decode("ascii")

def paytr_token(data, merchant_key, merchant_salt):
    """
    Compute PayTR HMAC token for get-token call.
    Order of concatenation is important.
    """
    hash_str = (
        str(data["merchant_id"])
        + data["user_ip"]
        + data["merchant_oid"]
        + data["email"]
        + str(data["payment_amount"])
        + data["user_basket"]
        + str(data["no_installment"])
        + str(data["max_installment"])
        + data["currency"]
        + str(data.get("test_mode", 0))
        + str(data.get("non_3d", 0))
    )
    digest = hmac.new(
        merchant_key.encode("utf-8"),
        (hash_str + merchant_salt).encode("utf-8"),
        hashlib.sha256,
    ).digest()
    import base64 as _b64
    return _b64.b64encode(digest).decode("ascii")
