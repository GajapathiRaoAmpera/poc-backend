import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.modules.voice import service
from tests.factories import login, make_user

API = "/api/v1"


def _settings(**kw) -> Settings:
    return Settings(mongodb_uri="mongodb://x", gemini_api_key=SecretStr("gk-123"), **kw)


async def test_token_request_is_locked_to_model_prompt_and_tool():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["key"] = str(req.url), req.headers["x-goog-api-key"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"name": "auth_tokens/abc"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await service.mint_token("budget", _settings(gemini_live_model="m-live"), http)
    assert out["token"] == "auth_tokens/abc" and out["model"] == "m-live"
    assert seen["url"] == service.TOKEN_URL and seen["key"] == "gk-123"
    body = seen["body"]
    assert body["uses"] == 1
    c = body["bidiGenerateContentSetup"]
    assert c["model"] == "models/m-live"
    assert "Budget Agent" in c["systemInstruction"]["parts"][0]["text"]
    fns = {f["name"]: f for f in c["tools"][0]["functionDeclarations"]}
    assert fns["send_to_agent"]["parameters"]["required"] == ["text"]
    assert set(fns) == {
        "send_to_agent",
        "get_status",
        "approve_revision",
        "request_changes",
        "resume_delivery",
        "cancel_delivery",
    }
    assert fns["approve_revision"]["parameters"]["required"] == ["stage", "revision"]
    assert out["agent"] == "Budget Agent" and out["voice"] == "Orus"


def test_each_agent_has_a_distinct_voice_and_overrides_apply():
    voices = {ch: service.live_config(ch, _settings()) for ch in service.LABELS}
    names = {
        v["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"]
        for v in voices.values()
    }
    assert len(names) == len(service.LABELS)
    cfg = service.live_config("code", _settings(gemini_live_voices={"code": "Fenrir"}))
    assert cfg["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Fenrir"
    assert "say 'confirm'" in cfg["systemInstruction"]["parts"][0]["text"]


async def test_provider_error_is_not_echoed():
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(400, text="secret detail"))
    )
    with pytest.raises(Exception) as exc:
        await service.mint_token("code", _settings(), http)
    assert "secret detail" not in str(exc.value) and exc.value.status == 502


async def test_missing_key_is_unavailable():
    s = Settings(mongodb_uri="mongodb://x", gemini_api_key=None)
    with pytest.raises(Exception) as exc:
        await service.mint_token("code", s)
    assert exc.value.code == "VOICE_UNAVAILABLE" and exc.value.status == 503


@pytest.fixture
def fake_mint(monkeypatch):
    async def _mint(channel, settings, http=None):
        return {
            "token": "t",
            "model": "m",
            "expires_at": "2030-01-01T00:00:00Z",
            "tool": "x",
            "agent": "A",
            "voice": "V",
        }

    monkeypatch.setattr(service, "mint_token", _mint)


async def test_session_requires_login(client, fake_mint):
    r = await client.post(
        f"{API}/projects/00000000-0000-0000-0000-000000000000/voice/session",
        json={"channel": "code"},
    )
    assert r.status_code == 401


async def test_session_for_own_project_and_not_others(client, db, fake_mint, monkeypatch):
    await make_user(db, email="a@x.com")
    await make_user(db, email="b@x.com")
    await login(client, "a@x.com")
    pid = (await client.post(f"{API}/projects", json={"name": "P"})).json()["id"]
    r = await client.post(f"{API}/projects/{pid}/voice/session", json={"channel": "intake"})
    assert r.status_code == 201 and r.json()["token"] == "t"
    assert await db.audit_logs.count_documents({"action": "voice.session"}) == 1

    monkeypatch.setattr(get_settings(), "voice_daily_session_limit", 1)
    r = await client.post(f"{API}/projects/{pid}/voice/session", json={"channel": "intake"})
    assert r.status_code == 429

    await client.post(f"{API}/auth/logout")
    await login(client, "b@x.com")
    r = await client.post(f"{API}/projects/{pid}/voice/session", json={"channel": "intake"})
    assert r.status_code == 404
