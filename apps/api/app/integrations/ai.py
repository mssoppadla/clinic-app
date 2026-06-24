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


def infer(message: str, ctx: dict) -> dict:
    """Return the agent's next decision for an inbound message + clinic context.
    AI config (mode/key/model) comes from the runtime config (superadmin platform tab), not code."""
    aicfg = get_effective("ai")
    if aicfg.get("mode") != "live" or not aicfg.get("api_key"):
        return _stub(message, ctx)
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=aicfg["api_key"])
        resp = client.messages.create(
            model=aicfg.get("model") or "claude-opus-4-8", max_tokens=512,
            thinking={"type": "adaptive"}, output_config={"effort": "low"},
            system=_system_prompt(ctx), tools=_TOOLS,
            messages=[{"role": "user", "content": message or ""}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                return {"type": "action", "tool": block.name, "args": dict(block.input or {})}
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return {"type": "reply", "text": text or "Sorry, could you rephrase that?"}
    except Exception as exc:                       # never break the webhook on an AI error
        log.warning("ai.infer live failed: %s", exc, extra={"event": "ai.fail"})
        return _stub(message, ctx)
