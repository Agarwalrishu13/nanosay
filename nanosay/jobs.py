"""The book of things in progress.

A reading you are listening to and a recording being written are both jobs: they
have a state, they show their working, they can be stopped, and the page watches
them by asking what has happened since it last looked. Keeping that in one small
place means the page has one way of talking to the app, not two.
"""

from __future__ import annotations

import threading
import time
import uuid

MAX_LOG_LINES = 400
KEEP_JOBS = 30


class Stopped(Exception):
    """Raised inside a job when somebody pressed Stop."""


class Job:
    kind = "job"

    def __init__(self, title: str):
        self.id = uuid.uuid4().hex[:12]
        self.title = title
        self.state = "running"          # running | done | error | stopped
        self.lines: list[str] = []
        self.progress = 0
        self.result: dict = {}
        self.error = ""
        self.started = time.time()
        self.ended = 0.0
        self.stopping = False
        self._lock = threading.Lock()

    # -- what the work calls ---------------------------------------------
    def log(self, message: str) -> None:
        text = str(message).rstrip()
        if not text:
            return
        with self._lock:
            self.lines.append(text)
            if len(self.lines) > MAX_LOG_LINES:
                del self.lines[:len(self.lines) - MAX_LOG_LINES]

    def set_progress(self, value) -> None:
        try:
            self.progress = max(0, min(100, int(value)))
        except (TypeError, ValueError):
            self.progress = 0

    def check(self) -> None:
        if self.stopping:
            raise Stopped()

    def finish(self, result: dict | None = None) -> None:
        self.result = dict(result or {})
        self.state = "done"
        self.progress = 100
        self.ended = time.time()

    def fail(self, message: str) -> None:
        self.error = str(message or "It did not finish.")
        self.state = "stopped" if self.stopping else "error"
        self.ended = time.time()

    # -- what the page asks for ------------------------------------------
    def payload(self, since: int = 0) -> dict:
        with self._lock:
            lines = list(self.lines)
        start = max(0, int(since))
        if start > len(lines):
            start = max(0, len(lines) - 100)
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "state": self.state,
            "progress": self.progress,
            "log": lines[start:],
            "cursor": len(lines),
            "error": self.error,
            "result": self.result if self.state in ("done", "error", "stopped") else {},
            "seconds": round((self.ended or time.time()) - self.started, 1),
        }

    def stop(self) -> bool:
        """Ask the job to stop. Subclasses that own a process override on_stop."""
        if self.state != "running":
            return False
        self.stopping = True
        self.on_stop()
        return True

    def on_stop(self) -> None:
        """Called when Stop is pressed — do whatever stops the work."""


class JobBook:
    """A small shelf of recent jobs, newest last."""

    def __init__(self) -> None:
        self._jobs: dict = {}
        self._order: list = []
        self._lock = threading.Lock()

    def start(self, title: str, work, job: Job | None = None) -> Job:
        job = job or Job(title)

        def run() -> None:
            try:
                work(job)
            except Stopped:
                job.error = "You stopped it."
                job.state = "stopped"
                job.ended = time.time()
            except ValueError as exc:                 # a readable refusal
                job.fail(str(exc))
            except Exception as exc:                  # a bug: say so, do not dump a trace
                job.log("Something inside nanoSay went wrong: %s" % exc)
                job.fail("Something inside nanoSay went wrong. The details are above.")

        self.remember(job)
        threading.Thread(target=run, name="nanosay-" + job.id, daemon=True).start()
        return job

    def remember(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > KEEP_JOBS:
                self._jobs.pop(self._order.pop(0), None)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 6) -> list:
        with self._lock:
            ids = list(self._order)[-limit:]
        out = []
        for job_id in reversed(ids):
            job = self.get(job_id)
            if job:
                out.append({"id": job.id, "title": job.title, "state": job.state,
                            "kind": job.kind, "when": job.started, "error": job.error})
        return out
