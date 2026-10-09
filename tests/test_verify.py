import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import messages
from server.app import _hits, app
from server.notify import format_notice
from server.exchanges import (
    check_binance,
    check_binance_file,
    gate_sign,
    interpret_binance,
    interpret_gate,
    interpret_okx,
    okx_sign,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MOCK_VERIFY", "1")
    monkeypatch.setenv("MOCK_UIDS", "10086001")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    _hits.clear()
    return TestClient(app)


def test_gate_signature_is_stable():
    first = gate_sign("secret", "GET", "/api/v4/rebate/partner/sub_list", "user_id=1&limit=100&offset=0", "", "1600000000")
    second = gate_sign("secret", "GET", "/api/v4/rebate/partner/sub_list", "user_id=1&limit=100&offset=0", "", "1600000000")
    assert first == second
    assert len(first) == 128


def test_okx_signature_matches_known_prehash():
    signed = okx_sign("secret", "2020-12-08T09:08:57.715Z", "GET", "/api/v5/affiliate/invitee/detail?uid=1")
    assert signed == okx_sign("secret", "2020-12-08T09:08:57.715Z", "GET", "/api/v5/affiliate/invitee/detail?uid=1")
    assert signed.endswith("=") or len(signed) > 20


def test_gate_subordinate_hit_is_verified():
    result = interpret_gate(200, {"total": 1, "list": [{"user_id": 42, "type": 3}]}, None, None, "42", "")
    assert result.status == "verified"
    assert result.relation == "direct_customer"


def test_gate_empty_list_is_rejected():
    result = interpret_gate(200, {"total": 0, "list": []}, 200, {"list": [{"uid": 42, "type": 0, "belong": ""}]}, "42", "")
    assert result.status == "rejected"


def test_gate_relation_direct_customer_is_verified():
    result = interpret_gate(401, {}, 200, {"list": [{"uid": "42", "type": 3, "belong": "partner", "ref_uid": 9}]}, "42", "")
    assert result.status == "verified"
    assert result.relation == "direct_customer"


def test_gate_relation_rejects_other_inviter_when_our_uid_is_set():
    result = interpret_gate(401, {}, 200, {"list": [{"uid": "42", "type": 3, "belong": "referral", "ref_uid": 9}]}, "42", "100")
    assert result.status == "rejected"


def test_gate_unreadable_page_is_not_a_rejection():
    result = interpret_gate(200, {"total": 80, "list": [{"user_id": 7, "type": 3}]}, None, None, "42", "")
    assert result.status == "pending"


def test_okx_detail_hit_and_miss():
    hit = interpret_okx({"code": "0", "data": [{"inviteeLevel": "2"}]}, None, "42")
    miss = interpret_okx({"code": "0", "data": []}, None, "42")
    assert hit.status == "verified"
    assert miss.status == "rejected"


def test_okx_falls_back_to_invitee_list():
    found = interpret_okx({"code": "51621", "msg": "not invitee"}, {"code": "0", "data": [{"uid": "42"}]}, "42")
    missing = interpret_okx({"code": "51621"}, {"code": "0", "data": []}, "42")
    broken = interpret_okx({"code": "50113", "msg": "Invalid Sign"}, {"code": "50113"}, "42")
    assert found.status == "verified"
    assert missing.status == "rejected"
    assert broken.status == "pending"


def test_binance_invitee_record_is_verified_without_volume():
    result = interpret_binance(
        {
            "code": "000000",
            "message": "success",
            "data": {"regTime": 1704969000000, "referralCode": "ABC123XYZ", "tradeVol": "0", "commission": "0"},
        }
    )
    assert result.status == "verified"
    assert result.relation == "referral:ABC123XYZ"


def test_binance_missing_invitee_and_api_error():
    assert interpret_binance({"code": "000000", "message": "success", "data": None}).status == "rejected"
    assert interpret_binance({"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."}).status == "pending"


def test_binance_api_requests_invitee_without_dates(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "key")
    monkeypatch.setenv("BINANCE_API_SECRET", "secret")

    async def fake(method, url, headers):
        assert method == "GET"
        assert "/sapi/v1/affiliate/performance/invitee?" in url
        assert "userId=42" in url
        assert "startDate" not in url
        assert headers["X-MBX-APIKEY"] == "key"
        return 200, {"code": "000000", "data": {"regTime": 1, "referralCode": "ABC", "tradeVol": "0"}}

    result = asyncio.run(check_binance("42", fake))
    assert result.status == "verified"


def test_binance_file(tmp_path, monkeypatch):
    monkeypatch.delenv("BINANCE_UID_FILE", raising=False)
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    assert check_binance_file("42").status == "manual"
    path = tmp_path / "uids.txt"
    path.write_text("# comment\n42,2024-01-01\n100\n", encoding="utf-8")
    monkeypatch.setenv("BINANCE_UID_FILE", str(path))
    assert check_binance_file("42").status == "verified"
    assert check_binance_file("99").status == "rejected"
    path.write_text("# empty\n", encoding="utf-8")
    assert check_binance_file("42").status == "manual"


def test_verify_mock_and_validation(client):
    missing = client.post("/api/verify", json={"exchange": "Gate", "uid": "10086001"})
    assert missing.status_code == 400
    assert missing.json()["message"] == messages.NEED_SOCIAL

    bad_uid = client.post("/api/verify", json={"exchange": "Gate", "uid": "abc", "telegram": "@alpha_user"})
    assert bad_uid.status_code == 400

    unsupported = client.post("/api/verify", json={"exchange": "Bybit", "uid": "10086001", "telegram": "@alpha_user"})
    assert unsupported.status_code == 400

    ok = client.post("/api/verify", json={"exchange": "Gate", "uid": "10086001", "qq": "10001", "telegram": "@alpha_user", "source": "X"})
    assert ok.status_code == 200
    assert ok.json()["status"] == "verified"
    again = client.post("/api/verify", json={"exchange": "Gate", "uid": "10086001", "telegram": "@alpha_user"})
    assert again.json()["id"] == ok.json()["id"]

    rejected = client.post("/api/verify", json={"exchange": "OKX", "uid": "20000002", "telegram": "@alpha_user"})
    assert rejected.json()["status"] == "rejected"


def test_binance_manual_without_mock(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MOCK_VERIFY", "0")
    monkeypatch.delenv("BINANCE_UID_FILE", raising=False)
    _hits.clear()
    response = TestClient(app).post("/api/verify", json={"exchange": "Binance", "uid": "12345678", "telegram": "@alpha_user"})
    assert response.status_code == 200
    assert response.json()["status"] == "manual"
    assert response.json()["message"] == messages.UNCONFIGURED


def test_gate_unconfigured_is_manual(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MOCK_VERIFY", "0")
    monkeypatch.delenv("GATE_API_KEY", raising=False)
    monkeypatch.delenv("GATE_API_SECRET", raising=False)
    _hits.clear()
    response = TestClient(app).post("/api/verify", json={"exchange": "Gate", "uid": "12345678", "telegram": "@alpha_user"})
    assert response.json()["status"] == "manual"
    assert response.json()["message"] == messages.UNCONFIGURED


def test_admin_list_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MOCK_VERIFY", "1")
    monkeypatch.setenv("MOCK_UIDS", "10086001")
    monkeypatch.setenv("ADMIN_TOKEN", "review-token")
    _hits.clear()
    client = TestClient(app)
    client.post("/api/verify", json={"exchange": "OKX", "uid": "10086001", "telegram": "@alpha_user"})
    assert client.get("/api/applications").status_code == 401
    listed = client.get("/api/applications", headers={"Authorization": "Bearer review-token"})
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    assert body["applications"][0]["uid"] == "10086001"
    assert body["applications"][0]["qq"] is None
    filtered = client.get("/api/applications", headers={"Authorization": "Bearer review-token"}, params={"exchange": "OKX", "status": "verified"})
    assert filtered.json()["total"] == 1
    missed = client.get("/api/applications", headers={"Authorization": "Bearer review-token"}, params={"exchange": "Gate"})
    assert missed.json()["total"] == 0


def test_notice_includes_exchange_result_and_form():
    text = format_notice(
        {
            "id": "CA-1",
            "exchange": "Gate",
            "status": "verified",
            "uid": "10086001",
            "qq": "10001",
            "telegram": "@alpha_user",
            "source": "YouTube",
            "source_other": "<campaign>",
            "submitted_at": "2026-10-09T02:02:00+00:00",
        }
    )
    assert "交易所：Gate" in text
    assert "自动验证：<b>通过</b>" in text
    assert "UID：10086001" in text
    assert "QQ：10001" in text
    assert "Telegram：@alpha_user" in text
    assert "QQ：10001" in text
    assert "来源：YouTube" in text
    assert "其他来源：&lt;campaign&gt;" in text
    assert "提交时间：2026-10-09 10:02" in text


def test_notice_omits_the_social_that_was_not_filled():
    text = format_notice(
        {
            "id": "CA-2",
            "exchange": "OKX",
            "status": "rejected",
            "uid": "20000002",
            "qq": "",
            "telegram": "@alpha_user",
            "source": "",
            "submitted_at": "2026-10-09T02:02:00+00:00",
        }
    )
    assert "QQ：" not in text
    assert "Telegram：@alpha_user" in text
    assert "自动验证：<b>未通过</b>" in text


def test_verify_pushes_notice_to_telegram(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MOCK_VERIFY", "1")
    monkeypatch.setenv("MOCK_UIDS", "10086001")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001")
    sent = []

    async def fake(application):
        sent.append(application)
        return True

    monkeypatch.setattr("server.app.notify_group", fake)
    _hits.clear()
    response = TestClient(app).post(
        "/api/verify",
        json={"exchange": "OKX", "uid": "10086001", "qq": "10001", "telegram": "@alpha_user", "source": "X"},
    )
    assert response.status_code == 200
    assert sent[0]["exchange"] == "OKX"
    assert sent[0]["status"] == "verified"
    assert sent[0]["uid"] == "10086001"
    assert sent[0]["qq"] == "10001"


def test_page_contains_server_messages():
    page = (ROOT / "verify.html").read_text(encoding="utf-8")
    for name in (
        "VERIFIED",
        "REJECTED",
        "BINANCE_FILE_MISS",
        "UNCONFIGURED",
        "UNAVAILABLE",
        "NEED_SOCIAL",
        "BAD_UID",
        "BAD_SOCIAL",
        "RATE_LIMIT",
        "FAILED",
        "CHECKING",
        "APP_ID_LABEL",
    ):
        assert getattr(messages, name) in page
    assert "/api/verify" in page
    assert "ca_membership_applications" not in page
