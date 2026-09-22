"""Asking nanoLaama — the AI on your own computer — for a shorter version.

This is the only part of nanoSay that talks to anything at all, and it only ever
talks to an address on *this* machine (127.0.0.1), which is where nanoLaama
listens when you have it open. Nothing is sent anywhere else, and nothing is
sent unless you press the button that says so.

If nanoLaama is not running, that is not an error — it is a normal thing for it
not to be running. The app says so in one sentence and carries on being useful.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

TIMEOUT = 15
LONG_TIMEOUT = 900

INSTRUCTIONS = (
    "Shorten the text below so it can be listened to in a couple of minutes. "
    "Keep the meaning, the names and the numbers. Write plain sentences a voice "
    "can read aloud: no headings, no bullet points, no markdown, no emoji. "
    "Answer with the shortened text only, and nothing else.\n\n"
)

MAX_INPUT_CHARS = 24000


def _get(address: str, path: str, timeout: int = TIMEOUT) -> dict:
    request = urllib.request.Request(address.rstrip("/") + path,
                                     headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def look(address: str) -> dict:
    """Is nanoLaama there, and is it ready to answer?"""
    address = (address or "").strip()
    if not address:
        return {"running": False, "ready": False,
                "sentence": "No address is set for nanoLaama."}
    try:
        status = _get(address, "/api/status")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError, json.JSONDecodeError):
        return {
            "running": False, "ready": False,
            "sentence": ("nanoLaama is not running on this computer at the moment. It would "
                         "be at %s — open it and press “Look again”." % address),
        }
    settings = status.get("settings") or {}
    engine = str(settings.get("engine") or "")
    model = str(settings.get("model") or "")
    ready = bool(engine and model)
    if ready:
        sentence = "nanoLaama is running and ready (using %s)." % (model or engine)
    else:
        sentence = ("nanoLaama is running, but no AI has been chosen in it yet. Open "
                    "nanoLaama, pick one, then come back.")
    return {"running": True, "ready": ready, "engine": engine, "model": model,
            "sentence": sentence}


def shorten(text: str, address: str, on_progress=None) -> tuple:
    """Ask the local AI for a shorter version. Returns (ok, text_or_reason)."""
    state = look(address)
    if not state.get("running"):
        return False, state["sentence"]
    if not state.get("ready"):
        return False, state["sentence"]

    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS]
        note = " (Only the beginning was sent — that is all the AI needs to summarise.)"
    else:
        note = ""

    payload = json.dumps({
        "engine": state["engine"],
        "model": state["model"],
        "messages": [{"role": "user", "content": INSTRUCTIONS + text}],
        "temperature": 0.3,
        "max_tokens": 900,
    }).encode("utf-8")

    request = urllib.request.Request(
        address.rstrip("/") + "/api/chat", data=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST")

    pieces: list = []
    try:
        with urllib.request.urlopen(request, timeout=LONG_TIMEOUT) as response:
            for raw in response:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    break
                try:
                    event = json.loads(body)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "delta":
                    pieces.append(str(event.get("text") or ""))
                    if on_progress and len(pieces) % 20 == 0:
                        on_progress(len("".join(pieces)))
                elif event.get("type") == "error":
                    return False, "nanoLaama said: %s" % event.get("message", "something went wrong")
    except urllib.error.URLError as exc:
        return False, ("The conversation with nanoLaama stopped (%s). It may not be "
                       "running any more." % exc)
    except (OSError, ValueError) as exc:
        return False, "Something went wrong talking to nanoLaama (%s)." % exc

    answer = "".join(pieces).strip()
    if not answer:
        return False, "nanoLaama did not say anything. Try again, or read the whole thing."
    return True, answer + note
