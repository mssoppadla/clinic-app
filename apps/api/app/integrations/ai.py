"""AI intent inference for the WhatsApp agent. Two modes (runtime-selectable, like the other
integrations): 'live' calls Claude with tool-use; 'stub' is a deterministic keyword responder
used by tests/local so the whole pipeline runs without an API key or network.

infer() returns a decision the agent executes:
  {"type": "reply",  "text": "..."}                          -> just send this text (FAQ / ask)
  {"type": "action", "tool": "queue_status", "args": {}}     -> run an action (agent executes it)
  {"type": "action", "tool": "join_queue",  "args": {"doctor_id": "..."}}
  {"type": "action", "tool": "book_slot",   "args": {"doctor_id": "...", "slot_id": "..."}}
"""
from __future__ import annotations

import json
import logging

from ..core.config import get_settings
from ..core.integration_config import get_effective

log = logging.getLogger("integrations.ai")

_TOOLS = [
    {"name": "queue_status", "description": "Tell the patient how many people are waiting now.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "join_queue", "description": "Add the patient to today's walk-in queue for a doctor.",
     "input_schema": {"type": "object",
                      "properties": {"doctor_id": {"type": "string", "description": "id from the doctor list"}},
                      "required": ["doctor_id"]}},
    {"name": "book_slot", "description": "Book a specific timed slot for the patient.",
     "input_schema": {"type": "object",
                      "properties": {"doctor_id": {"type": "string"},
                                     "slot_id": {"type": "string", "description": "id from the slot list"}},
                      "required": ["doctor_id", "slot_id"]}},
]


def _system_prompt(ctx: dict) -> str:
    docs = "\n".join(f"  - {d['name']} (id={d['id']})" for d in ctx.get("doctors", [])) or "  (none)"
    slots = "\n".join(f"  - {s['label']} with {s.get('doctor_name','')} (id={s['id']}, doctor_id={s['doctor_id']})"
                      for s in ctx.get("slots", [])) or "  (none today)"
    return (
        f"You are the WhatsApp booking assistant for {ctx.get('clinic_name','the clinic')}. "
        f"Be brief and friendly (this is WhatsApp). The patient's name is "
        f"{ctx.get('patient_name') or 'unknown'} and their phone is {ctx.get('phone')}.\n"
        f"Doctors:\n{docs}\nBookable slots:\n{slots}\n"
        f"Right now {ctx.get('queue_count',0)} patient(s) are in the walk-in queue "
        f"(~{ctx.get('avg_wait',0)} min wait).\n"
        "Help them check the queue, join the queue, or book a slot. Call the matching tool using "
        "ids from the lists above. If something is missing (e.g. which doctor), ask one short "
        "question instead of calling a tool. Never invent ids."
    )


def _stub(message: str, ctx: dict) -> dict:
    """Deterministic responder for tests/local — keyword routing over the provided context."""
    m = (message or "").lower()
    docs = ctx.get("doctors", [])
    slots = ctx.get("slots", [])
    if "status" in m or "how many" in m or "wait" in m:
        return {"type": "action", "tool": "queue_status", "args": {}}
    if "queue" in m or "walk" in m:
        if not docs:
            return {"type": "reply", "text": "Sorry, no doctors are available right now."}
        return {"type": "action", "tool": "join_queue", "args": {"doctor_id": docs[0]["id"]}}
    if "book" in m or "slot" in m or "appoint" in m:
        if not slots:
            return {"type": "reply", "text": "Sorry, there are no open slots right now. You can join the queue instead."}
        s = slots[0]
        return {"type": "action", "tool": "book_slot", "args": {"doctor_id": s["doctor_id"], "slot_id": s["id"]}}
    return {"type": "reply",
            "text": "Hi! I can help you book an appointment, join today's queue, or check the "
                    "queue status. What would you like to do?"}


# Sensible per-provider default model when the operator hasn't picked one.
_DEFAULT_MODEL = {"anthropic": "claude-opus-4-8", "openai": "gpt-4o-mini"}


def _infer_anthropic(message: str, ctx: dict, aicfg: dict) -> dict:
    """Claude via the Messages REST API (no SDK dependency)."""
    import httpx
    body = {
        "model": aicfg.get("model") or _DEFAULT_MODEL["anthropic"],
        "max_tokens": 512, "system": _system_prompt(ctx), "tools": _TOOLS,
        "messages": [{"role": "user", "content": message or ""}],
    }
    r = httpx.post("https://api.anthropic.com/v1/messages", json=body, timeout=30.0,
                   verify=get_settings().outbound_tls_verify,
                   headers={"x-api-key": aicfg["api_key"], "anthropic-version": "2023-06-01",
                            "content-type": "application/json"})
    r.raise_for_status()
    for block in r.json().get("content", []):
        if block.get("type") == "tool_use":
            return {"type": "action", "tool": block["name"], "args": dict(block.get("input") or {})}
    text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    return {"type": "reply", "text": text or "Sorry, could you rephrase that?"}


def _infer_openai(message: str, ctx: dict, aicfg: dict) -> dict:
    """OpenAI via Chat Completions with function-calling (no SDK dependency)."""
    import httpx
    tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                               "parameters": t["input_schema"]}} for t in _TOOLS]
    body = {
        "model": aicfg.get("model") or _DEFAULT_MODEL["openai"], "max_tokens": 512, "tools": tools,
        "messages": [{"role": "system", "content": _system_prompt(ctx)},
                     {"role": "user", "content": message or ""}],
    }
    r = httpx.post("https://api.openai.com/v1/chat/completions", json=body, timeout=30.0,
                   verify=get_settings().outbound_tls_verify,
                   headers={"Authorization": f"Bearer {aicfg['api_key']}", "Content-Type": "application/json"})
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    for tc in (msg.get("tool_calls") or []):
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        return {"type": "action", "tool": fn.get("name"), "args": args}
    return {"type": "reply", "text": msg.get("content") or "Sorry, could you rephrase that?"}


def infer(message: str, ctx: dict) -> dict:
    """Return the agent's next decision for an inbound message + clinic context. AI config
    (mode/provider/key/model) comes from runtime config (superadmin platform tab), not code.
    Provider is operator-selectable: 'anthropic' (Claude) or 'openai'. Calls the provider's REST
    API directly via httpx — no SDK dependency. Never breaks the webhook (falls back to the stub)."""
    aicfg = get_effective("ai")
    if aicfg.get("mode") != "live" or not aicfg.get("api_key"):
        return _stub(message, ctx)
    provider = (aicfg.get("provider") or "anthropic").lower()
    try:
        return _infer_openai(message, ctx, aicfg) if provider == "openai" \
            else _infer_anthropic(message, ctx, aicfg)
    except Exception as exc:                       # never break the webhook on an AI error
        log.warning("ai.infer live failed (provider=%s): %s", provider, exc, extra={"event": "ai.fail"})
        return _stub(message, ctx)
