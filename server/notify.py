"""Push each accepted application into the operations Telegram group."""

import logging
import os
from datetime import datetime, timedelta, timezone
from html import escape

import httpx

log = logging.getLogger(__name__)

STATUS_LABEL = {
    "verified": "通过",
    "rejected": "未通过",
    "manual": "待人工核对",
    "pending": "待人工核对",
}


def format_notice(application: dict) -> str:
    status = application.get("status") or ""
    label = STATUS_LABEL.get(status, "待人工核对")
    source = _text(application.get("source"))
    source_other = _text(application.get("source_other"))
    lines = [
        "<b>CRYPTO ALPHA · 新会员申请</b>",
        "",
        f"申请编号：{_text(application.get('id'))}",
        f"交易所：{_text(application.get('exchange'))}",
        f"自动验证：<b>{escape(label)}</b>",
    ]
    if application.get("repeat"):
        lines.append("说明：10 分钟内重复提交，沿用上次自动核对结果")
    lines.extend(["", f"UID：{_text(application.get('uid'))}"])
    if str(application.get("qq") or "").strip():
        lines.append(f"QQ：{_text(application.get('qq'))}")
    if str(application.get("telegram") or "").strip():
        lines.append(f"Telegram：{_text(application.get('telegram'))}")
    lines.extend(
        [
            f"来源：{source}",
            f"其他来源：{source_other}",
            f"提交时间：{_submitted_at(application.get('submitted_at'))}",
        ]
    )
    return "\n".join(lines)


def _text(value) -> str:
    text = str(value or "").strip()
    return escape(text) if text else "—"


def _submitted_at(value) -> str:
    if not value:
        moment = datetime.now(timezone.utc)
    else:
        moment = datetime.fromisoformat(str(value))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(timezone(timedelta(hours=8)))
    return escape(local.strftime("%Y-%m-%d %H:%M"))


async def notify_group(application: dict) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    try:
        await _post_telegram(token, chat_id, format_notice(application))
    except Exception:
        log.exception("telegram group notify failed")
        return False
    return True


async def _post_telegram(token: str, chat_id: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError("telegram sendMessage was not accepted")
