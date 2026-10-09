"""Affiliate UID checks for Gate, OKX, and Binance.

Binance uses the affiliate invitee performance endpoint. Dates are omitted so
the response is all-time identity, not a trading window that can be empty.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from server.messages import (
    BINANCE_FILE_MISS,
    REJECTED,
    UNAVAILABLE,
    UNCONFIGURED,
    VERIFIED,
)

GATE_TYPES = {
    1: "direct_agent",
    2: "indirect_agent",
    3: "direct_customer",
    4: "indirect_customer",
    5: "referral_user",
}


@dataclass
class CheckResult:
    status: str
    message: str
    relation: str = ""


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def gate_sign(secret: str, method: str, path: str, query: str, body: str, timestamp: str) -> str:
    hashed = hashlib.sha512(body.encode()).hexdigest()
    payload = f"{method}\n{path}\n{query}\n{hashed}\n{timestamp}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha512).hexdigest()


def okx_sign(secret: str, timestamp: str, method: str, path: str) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}{method}{path}".encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def okx_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def binance_sign(secret: str, query: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


async def http_json(method: str, url: str, headers: dict) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.request(method, url, headers=headers)
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {"data": data}
    return response.status_code, data


def load_binance_uids(path: str) -> set[str] | None:
    if not path or not os.path.isfile(path):
        return None
    found: set[str] = set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cell = line.split(",")[0].strip().strip('"').strip("'")
            if cell.isdigit():
                found.add(cell)
    return found


def _gate_relation_is_ours(item: dict | None, partner_uid: str) -> tuple[bool, str]:
    if not item:
        return False, ""
    try:
        kind = int(item.get("type") or 0)
    except (TypeError, ValueError):
        kind = 0
    belong = str(item.get("belong") or "").strip()
    ref_uid = str(item.get("ref_uid") or "").strip()
    if kind == 0 or belong == "":
        return False, ""
    relation = GATE_TYPES.get(kind, f"type_{kind}")
    if partner_uid and kind in {1, 3, 5} and ref_uid and ref_uid != partner_uid:
        return False, relation
    return True, relation


def interpret_gate(
    sub_status: int | None,
    sub_body: dict | None,
    rel_status: int | None,
    rel_body: dict | None,
    uid: str,
    partner_uid: str,
) -> CheckResult:
    """Decide from Gate responses. A successful miss is a rejection; transport
    or auth failures stay pending so a broken key cannot lock a real user out.
    """
    matched = False
    relation = ""
    authoritative = False

    if sub_status == 200 and isinstance(sub_body, dict):
        rows = sub_body.get("list") or []
        hit = next((row for row in rows if str(row.get("user_id") or "") == uid), None)
        if hit:
            matched = True
            authoritative = True
            try:
                kind = int(hit.get("type") or 0)
            except (TypeError, ValueError):
                kind = 0
            relation = {1: "sub_agent", 2: "indirect_customer", 3: "direct_customer"}.get(kind, "subordinate")
        else:
            try:
                total = int(sub_body.get("total") if sub_body.get("total") is not None else 0)
            except (TypeError, ValueError):
                total = 0
            if total == 0 and not rows:
                authoritative = True

    if not matched and rel_status == 200 and isinstance(rel_body, dict):
        authoritative = True
        rows = rel_body.get("list") or []
        item = next((row for row in rows if str(row.get("uid") or "") == uid), None)
        ok, relation = _gate_relation_is_ours(item, partner_uid)
        matched = ok

    if matched:
        return CheckResult("verified", VERIFIED, relation)
    if authoritative:
        return CheckResult("rejected", REJECTED, relation)
    return CheckResult("pending", UNAVAILABLE)


def interpret_okx(detail_body: dict | None, list_body: dict | None, uid: str) -> CheckResult:
    detail_code = str((detail_body or {}).get("code") or "")
    if detail_code == "0":
        rows = (detail_body or {}).get("data") or []
        if rows:
            level = str(rows[0].get("inviteeLevel") or "")
            relation = f"invitee:{level}" if level else "invitee"
            return CheckResult("verified", VERIFIED, relation)
        return CheckResult("rejected", REJECTED)

    if list_body is not None and str(list_body.get("code") or "") == "0":
        rows = list_body.get("data") or []
        for row in rows:
            if str(row.get("uid") or "") == uid:
                return CheckResult("verified", VERIFIED, "invitee")
        if not rows:
            return CheckResult("rejected", REJECTED)
    return CheckResult("pending", UNAVAILABLE)


BINANCE_INVITEE_KEYS = {
    "regTime",
    "referralCode",
    "kycTime",
    "firstDepositTime",
    "firstTradeTime",
    "vipLevel",
    "inviteeSpotRate",
    "inviteeFuturesRate",
}


def interpret_binance(body: dict | None) -> CheckResult:
    """A returned invitee record means the UID is ours, even when volume is zero."""
    if not isinstance(body, dict) or not body:
        return CheckResult("pending", UNAVAILABLE)

    code = body.get("code")
    message = str(body.get("message") or body.get("msg") or "")
    data = body.get("data") if "data" in body else None
    record = data if isinstance(data, dict) else body
    success = str(code) in {"000000", "0"} or (code is None and BINANCE_INVITEE_KEYS.intersection(record))
    if success and isinstance(record, dict) and BINANCE_INVITEE_KEYS.intersection(record):
        referral = str(record.get("referralCode") or "")
        return CheckResult("verified", VERIFIED, f"referral:{referral}" if referral else "invitee")
    if success and data in (None, {}, []):
        return CheckResult("rejected", REJECTED)

    lowered = message.lower()
    if any(token in lowered for token in ("not invitee", "invitee not", "user not found", "not your", "does not exist")):
        return CheckResult("rejected", REJECTED)
    if any(token in message for token in ("不存在", "不是你的", "非邀请", "非被邀请")):
        return CheckResult("rejected", REJECTED)
    return CheckResult("pending", UNAVAILABLE)


def check_binance_file(uid: str) -> CheckResult:
    listed = load_binance_uids(os.getenv("BINANCE_UID_FILE", "").strip())
    if not listed:
        return CheckResult("manual", UNCONFIGURED)
    if uid in listed:
        return CheckResult("verified", VERIFIED, "imported_list")
    return CheckResult("rejected", BINANCE_FILE_MISS)


async def check_binance(uid: str, request=http_json) -> CheckResult:
    key = os.getenv("BINANCE_API_KEY", "").strip()
    secret = os.getenv("BINANCE_API_SECRET", "").strip()
    if not key or not secret:
        return check_binance_file(uid)
    host = os.getenv("BINANCE_API_HOST", "https://api.binance.com").rstrip("/")
    timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    query = urlencode([("userId", uid), ("recvWindow", "5000"), ("timestamp", timestamp)])
    signature = binance_sign(secret, query)
    url = f"{host}/sapi/v1/affiliate/performance/invitee?{query}&signature={signature}"
    try:
        _, body = await request("GET", url, {"X-MBX-APIKEY": key})
    except httpx.HTTPError:
        return CheckResult("pending", UNAVAILABLE)
    return interpret_binance(body)


async def _gate_get(path: str, query: str, key: str, secret: str, host: str, request) -> tuple[int, dict]:
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    sign = gate_sign(secret, "GET", path, query, "", timestamp)
    url = f"{host}{path}" + (f"?{query}" if query else "")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "KEY": key,
        "Timestamp": timestamp,
        "SIGN": sign,
    }
    return await request("GET", url, headers)


async def check_gate(uid: str, request=http_json) -> CheckResult:
    key = os.getenv("GATE_API_KEY", "").strip()
    secret = os.getenv("GATE_API_SECRET", "").strip()
    if not key or not secret:
        return CheckResult("manual", UNCONFIGURED)
    host = os.getenv("GATE_API_HOST", "https://api.gateio.ws").rstrip("/")
    partner_uid = os.getenv("GATE_UID", "").strip()
    sub_status = sub_body = rel_status = rel_body = None
    try:
        query = urlencode([("user_id", uid), ("limit", "100"), ("offset", "0")])
        sub_status, sub_body = await _gate_get("/api/v4/rebate/partner/sub_list", query, key, secret, host, request)
    except httpx.HTTPError:
        sub_status = None
    try:
        query = urlencode([("user_id_list", uid)])
        rel_status, rel_body = await _gate_get("/api/v4/rebate/user/sub_relation", query, key, secret, host, request)
    except httpx.HTTPError:
        rel_status = None
    if sub_status is None and rel_status is None:
        return CheckResult("pending", UNAVAILABLE)
    return interpret_gate(sub_status, sub_body, rel_status, rel_body, uid, partner_uid)


async def check_okx(uid: str, request=http_json) -> CheckResult:
    key = os.getenv("OKX_API_KEY", "").strip()
    secret = os.getenv("OKX_API_SECRET", "").strip()
    passphrase = os.getenv("OKX_API_PASSPHRASE", "").strip()
    if not key or not secret or not passphrase:
        return CheckResult("manual", UNCONFIGURED)
    host = os.getenv("OKX_API_HOST", "https://www.okx.com").rstrip("/")

    async def signed_get(path: str) -> tuple[int, dict]:
        timestamp = okx_timestamp()
        headers = {
            "OK-ACCESS-KEY": key,
            "OK-ACCESS-SIGN": okx_sign(secret, timestamp, "GET", path),
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json",
        }
        return await request("GET", host + path, headers)

    detail_body = None
    list_body = None
    try:
        _, detail_body = await signed_get(f"/api/v5/affiliate/invitee/detail?uid={uid}")
    except httpx.HTTPError:
        detail_body = None
    decided = interpret_okx(detail_body, None, uid)
    if decided.status != "pending":
        return decided
    try:
        _, list_body = await signed_get(f"/api/v5/affiliate/invitee/list?uid={uid}")
    except httpx.HTTPError:
        list_body = None
    return interpret_okx(detail_body, list_body, uid)


async def check_exchange(exchange: str, uid: str, request=http_json) -> CheckResult:
    if _flag("MOCK_VERIFY"):
        allowed = {item.strip() for item in os.getenv("MOCK_UIDS", "").split(",") if item.strip()}
        if uid in allowed:
            return CheckResult("verified", VERIFIED, "mock")
        return CheckResult("rejected", REJECTED, "mock")
    if exchange == "Gate":
        return await check_gate(uid, request)
    if exchange == "OKX":
        return await check_okx(uid, request)
    if exchange == "Binance":
        return await check_binance(uid, request)
    return CheckResult("manual", UNCONFIGURED)
