"""Mints single-use Gemini Live tokens. The API key stays here; clients get a locked token."""

from datetime import timedelta
from typing import Any

import httpx

from app.core.config import Settings
from app.core.documents import now
from app.core.errors import AppError

TOKEN_URL = "https://generativelanguage.googleapis.com/v1beta/auth_tokens"  # noqa: S105 (an endpoint, not a secret)
TOOL_NAME = "send_to_agent"
# Actions the app only performs after the user says "confirm" (enforced client-side too).
CONFIRMED_TOOLS = ("approve_revision", "request_changes", "cancel_delivery")

LABELS = {
    "intake": "Project Orchestrator",
    "requirements": "Requirements Agent",
    "architecture": "Architecture Agent",
    "plan": "Plan Agent",
    "budget": "Budget Agent",
    "code": "Development Agent",
}
# Distinct prebuilt Gemini voices so each agent sounds like a different teammate.
VOICES = {
    "intake": "Zephyr",
    "requirements": "Kore",
    "architecture": "Charon",
    "plan": "Aoede",
    "budget": "Orus",
    "code": "Puck",
}

_STAGE = {"type": "STRING", "enum": ["requirements", "architecture", "plan", "budget", "code"]}


def _fn(name: str, description: str, **props: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {"type": "OBJECT", "properties": props}
    if props:
        params["required"] = list(props)
    return {"name": name, "description": description, "parameters": params}


def tools(channel: str) -> list[dict[str, Any]]:
    label = LABELS[channel]
    return [
        _fn(
            TOOL_NAME,
            f"Send the user's words to the {label} and return its reply.",
            text={"type": "STRING"},
        ),
        _fn("get_status", "Return where the delivery workflow is and what it is waiting for."),
        _fn(
            "approve_revision",
            "Approve an artifact revision. Only after the user said 'confirm'.",
            stage=_STAGE,
            revision={"type": "INTEGER"},
        ),
        _fn(
            "request_changes",
            "Request changes to a revision. Only after the user said 'confirm'.",
            stage=_STAGE,
            revision={"type": "INTEGER"},
            comment={"type": "STRING"},
        ),
        _fn("resume_delivery", "Resume a paused delivery workflow."),
        _fn("cancel_delivery", "Cancel the delivery workflow. Only after the user said 'confirm'."),
    ]


def system_instruction(channel: str) -> str:
    label = LABELS[channel]
    return (
        f"You are the {label}, speaking out loud in a project-delivery app where the whole "
        "delivery happens by voice. The real work is done by the app's agents; you never invent "
        "facts, numbers, requirements or decisions.\n"
        f"- For anything the user says to you, call {TOOL_NAME} with their exact words, say one "
        "short phrase like 'Working on it', then speak the result faithfully and concisely.\n"
        "- Text that starts with [UPDATE] is news from the delivery workflow: read it out "
        "naturally as yourself, keeping every fact and question. Do not call a tool for it.\n"
        "- Text that starts with [HANDOFF] means you just took over from another agent: greet "
        "the user briefly as yourself, then continue with the update it contains.\n"
        "- 'What's happening' or similar: call get_status and summarise it.\n"
        "- Approving, requesting changes and cancelling are high-impact. First read back exactly "
        "what will happen (stage, revision, comment) and ask the user to say 'confirm'. Only "
        "after they say 'confirm' call approve_revision, request_changes or cancel_delivery. If "
        "the result says it was not done, tell the user why.\n"
        "- 'Resume' or 'continue': call resume_delivery.\n"
        "- If the user interrupts you, stop at once and listen; never restart what you were "
        "saying from the beginning. Short interjections like 'wait', 'stop', 'hold on' or "
        "'one sec' mean stop and wait silently: no tool call, no reply beyond 'Sure'. If they "
        "change their mind mid-action ('no', 'never mind', 'cancel that') drop any pending "
        "read-back and don't call the action tool. If an interrupted update contained a question "
        "or an approval request, briefly re-offer it later in one sentence.\\n"
        "- Text starting with [RESUMED] lists updates the user missed while muted or away: give "
        "a short spoken recap, most important first (questions and approvals).\\n"
        "- Skip markdown symbols. If something is busy, blocked or failed, say so plainly and "
        "suggest the next step."
    )


def live_config(channel: str, settings: Settings) -> dict[str, Any]:
    voice = {**VOICES, **settings.gemini_live_voices}.get(channel, settings.gemini_live_voice)
    return {
        "responseModalities": ["AUDIO"],
        "systemInstruction": {"parts": [{"text": system_instruction(channel)}]},
        "tools": [{"functionDeclarations": tools(channel)}],
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
        "sessionResumption": {},
        "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
    }


def setup(channel: str, settings: Settings) -> dict[str, Any]:
    cfg = live_config(channel, settings)
    gen = {
        "responseModalities": cfg.pop("responseModalities"),
        "speechConfig": cfg.pop("speechConfig"),
    }
    return {"model": f"models/{settings.gemini_live_model}", "generationConfig": gen, **cfg}


async def mint_token(
    channel: str, settings: Settings, http: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""
    if not settings.voice_enabled or not key:
        raise AppError("VOICE_UNAVAILABLE", "Voice isn't set up on this server.", 503)
    expires = now() + timedelta(minutes=30)
    body = {
        "uses": 1,
        "expireTime": expires.isoformat().replace("+00:00", "Z"),
        "newSessionExpireTime": (now() + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        # REST shape of the SDK's liveConnectConstraints; no fieldMask = every field locked.
        "bidiGenerateContentSetup": setup(channel, settings),
    }
    client = http or httpx.AsyncClient()
    try:
        resp = await client.post(TOKEN_URL, json=body, headers={"x-goog-api-key": key}, timeout=15)
    except httpx.HTTPError as exc:
        raise AppError("VOICE_UNAVAILABLE", "Couldn't reach the voice service.", 502) from exc
    finally:
        if http is None:
            await client.aclose()
    if resp.status_code >= 400:
        # Never echo the provider body: it can contain request details.
        raise AppError(
            "VOICE_UNAVAILABLE",
            f"The voice service returned an error (HTTP {resp.status_code}).",
            502,
        )
    return {
        "token": resp.json()["name"],
        "model": settings.gemini_live_model,
        "expires_at": expires,
        "tool": TOOL_NAME,
        "agent": LABELS[channel],
        "voice": live_config(channel, settings)["speechConfig"]["voiceConfig"][
            "prebuiltVoiceConfig"
        ]["voiceName"],
    }
