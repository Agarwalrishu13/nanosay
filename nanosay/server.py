"""Every address the page talks to.

The shape of a session: **give it something** (a file, some pasted text, or a web
address) → **look at it** (how long it is, how long it will take to hear) →
**listen** (with the sentence being spoken highlighted) → **keep it** (a
recording you can play anywhere).

A reading is a background job, so a forty-page document never freezes the page.
"""

from __future__ import annotations

import atexit
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

from . import APP_NAME, __version__, jobs, reading, reader, shorten, store, voices
from .httpbase import App, Bytes, Error, Json

WEB_DIR = Path(__file__).parent / "web"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\- +()]+")

_uploads: dict[str, dict] = {}
_uploads_lock = threading.Lock()

_book = jobs.JobBook()
_readings: dict[str, reader.Reading] = {}
_readings_lock = threading.Lock()
KEEP_READINGS = 12


def _safe_filename(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", os.path.basename(name or "")).strip(" .")
    return cleaned[:160] or "document.txt"


def _inside(folder: Path, name: str) -> Path | None:
    target = (folder / _safe_filename(name)).resolve()
    try:
        target.relative_to(folder.resolve())
    except ValueError:
        return None
    return target


def _size_mb(path) -> float:
    try:
        return round(Path(path).stat().st_size / (1024 * 1024), 2)
    except OSError:
        return 0.0


def _reading_by_id(reading_id: str) -> reader.Reading | None:
    with _readings_lock:
        return _readings.get(reading_id)


def _remember_reading(session: reader.Reading) -> None:
    with _readings_lock:
        _readings[session.id] = session
        while len(_readings) > KEEP_READINGS:
            oldest = min(_readings.values(), key=lambda item: item.started)
            if oldest.state in ("reading", "paused"):
                break
            _readings.pop(oldest.id, None)


# --------------------------------------------------------------------------
# Turning something into sentences
# --------------------------------------------------------------------------
def _sentences_from_text(text: str) -> list:
    cleaned = reading.tidy_for_speaking(text or "")
    return reading.split_sentences(cleaned)


def _finish_session(session: reader.Reading) -> None:
    """Remember it in the library, so it is there next time."""
    store.remember({
        "title": session.title,
        "source": session.source,
        "sentences": session.total,
        "voice": session.voice,
        "shortened": session.shortened,
        "audio": "",
        "audio_name": "",
    })


# --------------------------------------------------------------------------
def create_app() -> App:
    app = App(APP_NAME, WEB_DIR, __version__)

    # ---------------------------------------------------------------- status
    @app.get("/api/health")
    def health(_request):
        return Json({"ok": True, "app": APP_NAME, "version": __version__})

    def _state() -> dict:
        return {
            "app": APP_NAME,
            "version": __version__,
            "python": sys.version.split()[0],
            "speech": voices.describe(),
            "settings": store.load_settings(),
            "library": store.load_library(),
            "readings": [session.payload() for session in _recent_readings()],
            "jobs": reader.recent(_book),
            "folder": str(store.recordings_dir()),
            "free_mb": store.free_space_mb(),
        }

    def _recent_readings() -> list:
        with _readings_lock:
            items = sorted(_readings.values(), key=lambda item: item.started, reverse=True)
        return items[:6]

    @app.get("/api/state")
    def state(_request):
        return Json(_state())

    @app.post("/api/look-again")
    def look_again(_request):
        """Re-check the voices and whether nanoLaama is running."""
        voices.refresh()
        state_now = _state()
        state_now["laama"] = shorten.look(state_now["settings"].get("address", ""))
        if not state_now["speech"]["available"]:
            state_now["message"] = state_now["speech"]["note"]
        else:
            state_now["message"] = "These are the voices on this computer: %s." % ", ".join(
                voice["name"] for voice in state_now["speech"]["voices"][:4])
        return Json(state_now)

    @app.get("/api/laama")
    def laama(_request):
        """Is the local AI there, and could it shorten something for us?"""
        return Json(shorten.look(store.load_settings().get("address", "")))

    @app.post("/api/settings")
    def settings(request):
        patch = request.json()
        if not isinstance(patch, dict):
            return Error("Settings must be named values.")
        return Json({"settings": store.save_settings(patch)})

    # ---------------------------------------------------------------- upload
    @app.post("/api/upload/start")
    def upload_start(request):
        name = _safe_filename(str(request.json().get("name", "document.txt")))
        upload_id = uuid.uuid4().hex
        target = store.uploads_dir() / (upload_id + ".part")
        target.write_bytes(b"")
        with _uploads_lock:
            _uploads[upload_id] = {"name": name, "path": target, "received": 0}
        return Json({"id": upload_id, "name": name, "chunk_bytes": 4 * 1024 * 1024})

    @app.post("/api/upload/chunk")
    def upload_chunk(request):
        upload_id = request.q("id", "")
        with _uploads_lock:
            entry = _uploads.get(upload_id)
        if not entry:
            return Error("That upload expired. Start again.")
        try:
            offset = int(request.q("offset", "-1"))
        except ValueError:
            return Error("Bad offset.")
        if offset != entry["received"]:
            return Error("The pieces arrived out of order.", 409, expected=entry["received"])
        with open(entry["path"], "ab") as handle:
            handle.write(request.body)
        entry["received"] += len(request.body)
        return Json({"received": entry["received"]})

    @app.post("/api/upload/finish")
    def upload_finish(request):
        upload_id = str(request.json().get("id", ""))
        with _uploads_lock:
            entry = _uploads.pop(upload_id, None)
        if not entry:
            return Error("That upload expired. Start again.")
        destination = store.uploads_dir() / entry["name"]
        os.replace(entry["path"], destination)
        try:
            return Json(_open_document(str(destination), entry["name"]))
        except reading.Unreadable as exc:
            return Error(str(exc))

    def _open_document(path: str, name: str = "") -> dict:
        """Everything the page shows once it has something to read."""
        text, note = reading.extract(path)
        sentences = _sentences_from_text(text)
        if not sentences:
            raise reading.Unreadable("There was no readable text in that file.")
        settings_now = store.load_settings()
        settings_now["rate"] = settings_now.get("rate") or voices.describe()["rate"]["default"]
        return {
            "name": name or os.path.basename(path),
            "path": path,
            "note": note,
            "title": reading.title_for(path),
            "kind": "document",
            "sentences": sentences,
            "text": text[:200000],
            "estimate": reading.estimate(sentences, settings_now.get("rate") or 0),
            "words": reading.words(text),
        }

    # ------------------------------------------------------------------ words
    @app.post("/api/paste")
    def paste(request):
        """Text typed or pasted straight into the page."""
        text = str(request.json().get("text", ""))
        if not text.strip():
            return Error("There is nothing to read yet — type or paste some words first.")
        sentences = _sentences_from_text(text)
        if not sentences:
            return Error("I could not find any words in that.")
        settings_now = store.load_settings()
        return Json({
            "name": "words you pasted",
            "path": "",
            "note": "Pasted straight into the page.",
            "title": "Something I pasted",
            "kind": "words",
            "sentences": sentences,
            "text": text[:200000],
            "estimate": reading.estimate(sentences, settings_now.get("rate") or 0),
            "words": reading.words(text),
        })

    @app.post("/api/address")
    def from_address(request):
        """Fetch a web page and take the words out of it."""
        address = str(request.json().get("url", "")).strip()
        parsed = urlparse(address)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return Error("That does not look like a web address. It should start with "
                         "https:// and look like a page you could open in a browser.")
        try:
            request_object = urllib.request.Request(
                address, headers={"User-Agent": "nanoSay (reading a page aloud)"})
            with urllib.request.urlopen(request_object, timeout=25) as response:
                raw = response.read(3_000_000)
            markup = raw.decode("utf-8", errors="replace")
        except Exception as exc:
            return Error("I could not fetch that page (%s). Check the address, or paste "
                         "the words in instead." % exc)
        text = reading.text_from_html(markup)
        sentences = _sentences_from_text(text)
        if len(sentences) < 2:
            return Error("That page had almost no words on it — it may be a page that "
                         "loads its text with scripts, which nanoSay cannot follow.")
        return Json({
            "name": parsed.netloc,
            "path": "",
            "note": "Read from %s" % address,
            "title": parsed.netloc,
            "kind": "page",
            "address": address,
            "sentences": sentences,
            "text": text[:200000],
            "estimate": reading.estimate(sentences, store.load_settings().get("rate") or 0),
            "words": reading.words(text),
        })

    # ----------------------------------------------------------------- samples
    @app.get("/api/samples")
    def samples(_request):
        return Json({"samples": [
            {"id": "letter", "title": "A short letter",
             "blurb": "Three paragraphs. The quickest way to hear a voice."},
            {"id": "tricky", "title": "Sentences that trip up readers",
             "blurb": "Abbreviations, decimals and initials — the hard cases."},
            {"id": "story", "title": "A longer piece to settle into",
             "blurb": "Six short paragraphs, about a minute of listening."},
        ]})

    @app.post("/api/samples/load")
    def sample_load(request):
        wanted = str(request.json().get("id", ""))
        text = SAMPLE_TEXTS.get(wanted)
        if not text:
            return Error("I do not have an example called that.")
        sentences = _sentences_from_text(text)
        return Json({
            "name": "example: " + wanted,
            "path": "",
            "note": "An example that came with nanoSay.",
            "title": SAMPLE_TITLES[wanted],
            "kind": "sample",
            "sentences": sentences,
            "text": text,
            "estimate": reading.estimate(sentences, store.load_settings().get("rate") or 0),
            "words": reading.words(text),
        })

    # ---------------------------------------------------------------- shorten
    @app.post("/api/shorten")
    def do_shorten(request):
        """Ask the local AI for a shorter version of the text."""
        body = request.json()
        sentences = [str(item) for item in (body.get("sentences") or [])]
        if not sentences:
            return Error("There is nothing to shorten yet.")
        address = str(body.get("address") or store.load_settings().get("address", ""))
        job = _book.start("Shorten with nanoLaama", _shorten_work(sentences, address))
        return Json({"job_id": job.id, "message": "Asking nanoLaama for a shorter version…"})

    def _shorten_work(sentences: list, address: str):
        def work(job: jobs.Job) -> None:
            job.log("Asking nanoLaama for a shorter version…")
            ok, answer = shorten.shorten(" ".join(sentences), address)
            if not ok:
                job.fail(answer)
                return
            shortened = _sentences_from_text(answer)
            if not shortened:
                job.fail("nanoLaama answered, but there were no sentences in it.")
                return
            job.log("nanoLaama wrote a shorter version: %d sentences instead of %d."
                    % (len(shortened), len(sentences)))
            job.finish({
                "sentences": shortened,
                "text": " ".join(shortened),
                "estimate": reading.estimate(shortened, 0),
                "sentence": "Shortened to %d sentences." % len(shortened),
                "note": "Written by nanoLaama on this computer. Read it through once — "
                        "shortening can lose a detail that mattered to you.",
            })
        return work

    # ----------------------------------------------------------------- listen
    @app.post("/api/read")
    def start_reading(request):
        body = request.json()
        sentences = [str(item) for item in (body.get("sentences") or [])]
        if not sentences:
            return Error("There is nothing to read yet.")
        settings_now = store.load_settings()
        voice = str(body.get("voice") or settings_now.get("voice") or "")
        rate = body.get("rate")
        rate = int(settings_now["rate"] or 0) if rate is None else int(rate)
        volume = int(body.get("volume") if body.get("volume") is not None else settings_now.get("volume", 100))
        title = str(body.get("title") or "Reading")[:80]
        source = str(body.get("source") or "")

        if voices.engine() is None:
            return Error(voices.describe()["note"])

        with _readings_lock:
            running = [session for session in _readings.values() if session.state == "reading"]
        for session in running:
            session.stop()

        session = reader.Reading(title, sentences, voice, rate, volume, source=source)
        _remember_reading(session)
        store.save_settings({"voice": voice, "rate": rate, "volume": volume})
        _book.start(title, lambda job: job.run(), job=session)
        return Json({"reading_id": session.id, "total": session.total,
                     "message": "Reading out loud."})

    @app.get("/api/reading/{reading_id}")
    def reading_state(request):
        session = _reading_by_id(request.params["reading_id"])
        if not session:
            return Error("I lost track of that reading.", 404)
        return Json(session.payload(since=request.q_int("since", 0)))

    @app.post("/api/reading/{reading_id}/{action}")
    def reading_action(request):
        session = _reading_by_id(request.params["reading_id"])
        if not session:
            return Error("I lost track of that reading.", 404)
        action = request.params["action"]
        if action == "pause":
            session.pause()
            return Json({"state": session.state, "message": "Paused."})
        if action == "resume":
            if session.state == "paused":
                session.resume()
                return Json({"state": session.state, "message": "Carrying on."})
            return Error("That reading is not paused. Press play to start again.")
        if action == "skip":
            session.skip()
            return Json({"state": session.state, "index": session.index,
                         "message": "Skipped to the next sentence."})
        if action == "stop":
            session.stop()
            if session.total:
                _finish_session(session)
            return Json({"state": session.state, "message": "Stopped."})
        return Error("I do not know how to do that with a reading.")

    # --------------------------------------------------------------- record
    @app.post("/api/record")
    def record(request):
        body = request.json()
        sentences = [str(item) for item in (body.get("sentences") or [])]
        if not sentences:
            return Error("There is nothing to record yet.")
        settings_now = store.load_settings()
        voice = str(body.get("voice") or settings_now.get("voice") or "")
        rate = body.get("rate")
        rate = int(settings_now["rate"] or 0) if rate is None else int(rate)
        title = str(body.get("title") or "reading")[:60]
        job = reader.make_recording(title, sentences, voice, rate, _book)
        return Json({"job_id": job.id, "message": "Recording started. It runs faster than "
                                                 "reading aloud does."})

    @app.get("/api/job/{job_id}")
    def job_state(request):
        job = _book.get(request.params["job_id"])
        if not job:
            return Error("I lost track of that job — it may be one of the older ones.", 404)
        return Json(job.payload(since=request.q_int("since", 0)))

    @app.post("/api/job/{job_id}/stop")
    def job_stop(request):
        job = _book.get(request.params["job_id"])
        if not job or not job.stop():
            return Error("That job is not running any more.")
        return Json({"message": "Stopped."})

    # -------------------------------------------------------------- downloads
    @app.get("/api/download/{name}")
    def download(request):
        target = _inside(store.recordings_dir(), request.params["name"])
        if target is None:
            return Error("Not allowed.", 403)
        if not target.is_file():
            return Error("That recording is gone. nanoSay clears out recordings it made "
                         "after a week so they cannot fill your disk.", 404)
        content = "audio/wav" if target.suffix.lower() == ".wav" else "application/octet-stream"
        return Bytes(target.read_bytes(), content)

    @app.post("/api/reveal")
    def reveal(request):
        """Open the recordings folder — the answer to “where did it go?”."""
        folder = store.recordings_dir()
        wanted = str(request.json().get("name", ""))
        if wanted:
            candidate = _inside(folder, wanted)
            if candidate is not None and candidate.is_file():
                folder = candidate.parent
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(folder))                    # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except (OSError, AttributeError) as exc:
            return Json({"opened": False, "folder": str(folder),
                         "message": "I could not open a folder window (%s). Your "
                                    "recordings are in %s" % (exc, folder)})
        return Json({"opened": True, "folder": str(folder), "message": "Opened %s" % folder})

    @app.post("/api/forget")
    def forget(request):
        what = str(request.json().get("what", "library"))
        if what == "library":
            store.forget()
            return Json({"message": "The list is cleared. Recordings already made are still "
                                    "in the folder.", "library": []})
        if what == "all":
            outcome = store.clear_recordings()
            return Json({"message": "Removed %d recording%s and freed %.1f MB."
                                    % (outcome["removed"], "" if outcome["removed"] == 1 else "s",
                                       outcome["freed_mb"]), "library": []})
        return Error("I do not know how to forget that.")

    @app.get("/api/about")
    def about(_request):
        speech = voices.describe()
        return Json({
            "app": APP_NAME,
            "version": __version__,
            "folder": str(store.recordings_dir()),
            "offline": True,
            "speech": {"engine": speech["engine"], "label": speech["label"],
                       "note": speech["note"], "voices": len(speech["voices"])},
            "siblings": [
                {"name": "nanolaama", "url": "https://github.com/Agarwalrishu13/nanolaama",
                 "what": "talk to an AI on your own computer"},
                {"name": "nanolearn", "url": "https://github.com/Agarwalrishu13/nanolearn",
                 "what": "drop a spreadsheet, get an answer machine"},
                {"name": "nanowrap", "url": "https://github.com/Agarwalrishu13/nanowrap",
                 "what": "the programs on your computer, with buttons"},
                {"name": "nanodoc", "url": "https://github.com/Agarwalrishu13/nanodoc",
                 "what": "ask a document questions"},
                {"name": "nonoforge", "url": "https://github.com/Agarwalrishu13/nonoforge",
                 "what": "make a whole project without coding"},
                {"name": "nanohome", "url": "https://github.com/Agarwalrishu13/nanohome",
                 "what": "one window for all of these"},
            ],
        })

    atexit.register(store.clear_given_files)
    return app


# --------------------------------------------------------------------------
# The examples that come with the app
# --------------------------------------------------------------------------
SAMPLE_TITLES = {
    "letter": "A short letter",
    "tricky": "Sentences that trip up readers",
    "story": "A longer piece to settle into",
}

SAMPLE_TEXTS = {
    "letter": (
        "Dear Auntie Meera,\n\n"
        "Thank you for the parcel. The biscuits did not survive the journey, but the "
        "photographs did, and I have put the one of the boat in a frame. It is on the "
        "shelf next to the window, where the light gets it in the afternoon.\n\n"
        "We are coming up on the 14th of next month and staying for four days. I will "
        "bring the good coffee. Please do not tidy up on our account — you know we do "
        "not mind.\n\n"
        "Love,\nRavi"
    ),
    "tricky": (
        "Dr. Smith paid 3.50 for the tickets on 4 p.m. on Tue. 12 Mar., which struck "
        "him as steep for a film he had already seen twice.\n\n"
        "J. K. Rowling, R. L. Stevenson and A. A. Milne all wrote for children, and so "
        "did E. B. White. The chapter runs from p. 14 to p. 29 — about 2,400 words, "
        "according to the publisher, i.e. roughly ten minutes of reading aloud.\n\n"
        "She said, “Wait — are you coming?” and he said nothing at all. That silence "
        "lasted exactly as long as it needed to."
    ),
    "story": (
        "The lighthouse keeper kept a ledger of everything the sea gave back. It began, "
        "as most ledgers do, with an accident: a brass compass, green with salt, found "
        "on the shingle after a November storm.\n\n"
        "He wrote the date, the weather, and where he had found it. He wrote what he "
        "thought it had been used for and by whom, which was guesswork, and he knew it. "
        "The guessing was the pleasure.\n\n"
        "Over eleven years the ledger filled up. A child's shoe. A wooden spoon worn "
        "thin in the middle. Half a postcard with the words see you soon still legible "
        "under the water stains, and no name anywhere on it.\n\n"
        "People came to the island to look at the ledger. They asked which find had "
        "mattered most, and he always said the same thing: the next one. That answer "
        "annoyed them, and he did not mind. It was true.\n\n"
        "In his last summer he wrote an entry with nothing in the weather column and a "
        "single line where the description should have been. It said: the sea keeps "
        "better records than I do.\n\n"
        "The ledger is in the county library now, in a glass case, open at that page."
    ),
}
