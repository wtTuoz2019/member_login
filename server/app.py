import hmac
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from server.exchanges import check_exchange
from server.notify import notify_group
from server.messages import (
    APP_ID_LABEL,
    BAD_SOCIAL,
    BAD_UID,
    FAILED,
    NEED_SOCIAL,
    RATE_LIMIT,
    REJECTED,
    UNAVAILABLE,
    UNSUPPORTED,
    VERIFIED,
)
from server.store import query_applications, reuse_recent, save_application

ROOT = Path(__file__).resolve().parent.parent
EXCHANGES = {"gate": "Gate", "okx": "OKX", "binance": "Binance"}
_hits: dict[str, list[float]] = defaultdict(list)


class VerifyIn(BaseModel):
    exchange: str = ""
    uid: str = ""
    qq: str = ""
    telegram: str = ""
    source: str = ""
    sourceOther: str = ""


app = FastAPI(title="CRYPTO ALPHA UID verification", docs_url=None, redoc_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _client_ip(request: Request) -> str:
    if os.getenv("TRUST_PROXY", "").strip() == "1":
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _allow(ip: str) -> bool:
    now = time.time()
    recent = [stamp for stamp in _hits[ip] if now - stamp < 600]
    if len(recent) >= 8:
        _hits[ip] = recent
        return False
    recent.append(now)
    _hits[ip] = recent
    return True


def _new_id() -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    number = int(time.time() * 1000)
    chars = []
    while number:
        number, rest = divmod(number, 36)
        chars.append(alphabet[rest])
    return "CA-" + ("".join(reversed(chars)) or "0")


def _clean_social(value: str, limit: int) -> str:
    return " ".join(value.strip().split())[:limit]


@app.get("/")
def index():
    return FileResponse(ROOT / "verify.html")


@app.get("/verify.html")
def verify_page():
    return FileResponse(ROOT / "verify.html")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "mock": os.getenv("MOCK_VERIFY", "").strip().lower() in {"1", "true", "yes"},
        "exchanges": {
            "gate": bool(os.getenv("GATE_API_KEY") and os.getenv("GATE_API_SECRET")),
            "okx": bool(os.getenv("OKX_API_KEY") and os.getenv("OKX_API_SECRET") and os.getenv("OKX_API_PASSPHRASE")),
            "binance": bool(os.getenv("BINANCE_API_KEY") and os.getenv("BINANCE_API_SECRET")),
        },
        "telegram": bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")),
    }


@app.get("/api/applications")
def applications(
    authorization: str | None = Header(default=None),
    exchange: str = "",
    status: str = "",
    uid: str = "",
    q: str = "",
    limit: int = 50,
    offset: int = 0,
):
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    provided = (authorization or "").removeprefix("Bearer ").strip()
    if not expected or not provided or not hmac.compare_digest(provided.encode(), expected.encode()):
        return JSONResponse({"message": "unauthorized"}, status_code=401)
    return query_applications(exchange=exchange, status=status, uid=uid, q=q, limit=limit, offset=offset)


@app.post("/api/verify")
async def verify(body: VerifyIn, request: Request):
    if not _allow(_client_ip(request)):
        return JSONResponse({"message": RATE_LIMIT}, status_code=429)

    exchange = EXCHANGES.get(body.exchange.strip().lower())
    uid = body.uid.strip()
    qq = _clean_social(body.qq, 40)
    telegram = _clean_social(body.telegram, 64)
    source = _clean_social(body.source, 40)
    source_other = _clean_social(body.sourceOther, 100)

    if exchange is None:
        return JSONResponse({"message": UNSUPPORTED}, status_code=400)
    if not uid.isdigit() or not 4 <= len(uid) <= 32:
        return JSONResponse({"message": BAD_UID}, status_code=400)
    if not qq and not telegram:
        return JSONResponse({"message": NEED_SOCIAL}, status_code=400)
    if (qq and not qq.isdigit()) or (telegram and not _telegram_ok(telegram)):
        return JSONResponse({"message": BAD_SOCIAL}, status_code=400)

    contact = {"qq": qq, "telegram": telegram, "source": source, "source_other": source_other}
    submitted_at = datetime.now(timezone.utc).isoformat()
    cached = reuse_recent(exchange, uid, contact)
    if cached:
        await notify_group(
            {
                "id": cached["id"],
                "exchange": exchange,
                "uid": uid,
                "status": cached["status"],
                "submitted_at": submitted_at,
                "repeat": True,
                **contact,
            }
        )
        return _payload(cached["id"], cached["status"], _message_for(cached["status"]))

    try:
        result = await check_exchange(exchange, uid)
    except Exception:
        return JSONResponse({"message": FAILED}, status_code=500)

    application_id = _new_id()
    save_application(
        {
            "id": application_id,
            "exchange": exchange,
            "uid": uid,
            "qq": qq,
            "telegram": telegram,
            "source": source,
            "source_other": source_other,
            "status": result.status,
            "relation": result.relation,
            "created_at": submitted_at,
        }
    )
    await notify_group(
        {
            "id": application_id,
            "exchange": exchange,
            "uid": uid,
            "status": result.status,
            "submitted_at": submitted_at,
            "repeat": False,
            **contact,
        }
    )
    return _payload(application_id, result.status, result.message)


def _telegram_ok(value: str) -> bool:
    handle = value[1:] if value.startswith("@") else value
    if not 4 <= len(handle) <= 32:
        return False
    return handle.replace("_", "").isalnum()


def _message_for(status: str) -> str:
    if status == "verified":
        return VERIFIED
    if status == "rejected":
        return REJECTED
    return UNAVAILABLE


def _payload(application_id: str, status: str, message: str) -> dict:
    return {"id": application_id, "status": status, "message": message, "applicationLabel": APP_ID_LABEL}
