"""Reading out loud, and recording it.

A **reading** is a live session: the voice speaks, the page highlights each
sentence as it is reached, and you can pause, carry on, or stop. Pausing works
by stopping the voice where it is and remembering which sentence it was on —
so carrying on repeats that sentence from its start rather than in the middle
of a word.

A **recording** is the same words written into a file you can keep, send, or
play on a phone. Where the voice engine writes one file per piece (Windows and
eSpeak) the pieces are recorded one at a time so the progress bar can tell the
truth, then joined into one recording.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from . import jobs, reading, store, voices

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ==========================================================================
# A reading you are listening to
# ==========================================================================
class Reading(jobs.Job):
    """One session of being read to."""

    kind = "reading"

    def __init__(self, title: str, sentences: list, voice: str, rate: int,
                 volume: int, source: str = "", shortened: bool = False):
        super().__init__(title)
        # A reading starts out reading; there is no waiting-to-start state,
        # because pressing play is what creates it.
        self.state = "reading"
        self.sentences = list(sentences)
        self.total = len(self.sentences)
        self.index = 0                     # the sentence being spoken (or next)
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.source = source
        self.shortened = shortened
        self.process: subprocess.Popen | None = None
        self.engine_id = (voices.engine().id if voices.engine() else "")
        self.resumed = 0

    # -- what the page asks for ------------------------------------------
    def payload(self, since: int = 0) -> dict:
        payload = super().payload(since)
        payload.update({
            "index": self.index,
            "total": self.total,
            "speaking": self.sentences[self.index] if self.index < self.total else "",
            "voice": self.voice,
            "rate": self.rate,
            "engine": self.engine_id,
            "source": self.source,
            "shortened": self.shortened,
            "paused": self.state == "paused",
        })
        return payload

    # -- controls ---------------------------------------------------------
    def pause(self) -> bool:
        if self.state != "reading":
            return False
        self.state = "paused"
        self._end_process()
        return True

    def resume(self) -> bool:
        if self.state != "paused":
            return False
        self.state = "reading"
        return True

    def skip(self) -> bool:
        """Jump to the next sentence."""
        if self.index < self.total:
            self.index += 1
        self._end_process()
        if self.state in ("reading", "paused") and self.index >= self.total:
            self.state = "done"
            self.ended = time.time()
        return True

    def stop(self) -> bool:
        if self.state not in ("reading", "paused"):
            return False
        self.stopping = True
        self.state = "stopped"
        self.ended = time.time()
        self._end_process()
        return True

    def on_stop(self) -> None:
        self._end_process()

    def _end_process(self) -> None:
        process = self.process
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    # -- the work ---------------------------------------------------------
    def run(self) -> None:
        self.log("Reading “%s” — %d sentences." % (self.title, self.total))
        try:
            while self.state in ("reading", "paused"):
                if self.state == "paused":
                    time.sleep(0.2)
                    continue
                if self.index >= self.total:
                    self.state = "done"
                    break
                if getattr(voices.engine(), "queue_mode", False):
                    self._read_by_the_page()
                else:
                    self._read_one_at_a_time()
        except ValueError as exc:
            self.state = "error"
            self.error = str(exc)
        except Exception as exc:
            self.state = "error"
            self.error = ("The voice stopped unexpectedly (%s). You can press play to "
                          "carry on from where it got to." % exc)
        finally:
            self._end_process()
            if self.state == "done":
                self.log("Finished.")
                self.progress = 100
            self.ended = time.time()

    def _read_by_the_page(self) -> None:
        """Windows: one process reads the rest and counts the sentences."""
        remaining = self.sentences[self.index:]
        queue = store.scratch_dir() / ("reading-%s.txt" % self.id)
        process, _queue_mode = voices.open_reading(remaining, self.voice, self.rate, self.volume, queue)
        self.process = process
        base = self.index
        assert process.stdout is not None
        try:
            for line in process.stdout:
                if self.state != "reading":
                    break
                found = line.strip()
                if found.startswith("NANO"):
                    value = found[5:].strip()
                    if value.isdigit():
                        self.index = base + int(value)
                        self.progress = int(self.index * 100 / max(1, self.total))
        finally:
            try:
                process.stdout.close()
            except OSError:
                pass
        process.wait()
        self.process = None
        if self.state == "reading" and self.index < self.total:
            # The voice stopped without saying it was done: count the sentence it
            # had announced as read, and let the loop decide what to do next.
            self.index = min(self.total, self.index + 1)

    def _read_one_at_a_time(self) -> None:
        """macOS and Linux: one short-lived process per sentence."""
        sentence = self.sentences[self.index]
        process = voices.open_sentence(sentence, self.voice, self.rate, self.volume)
        self.process = process
        process.wait()
        self.process = None
        if self.state == "reading":
            self.index += 1
            self.progress = int(self.index * 100 / max(1, self.total))


# ==========================================================================
# A recording you keep
# ==========================================================================
def _save_in_one_go(job: jobs.Job, text: str, target: Path, voice: str, rate: int) -> None:
    job.log("Recording in one go. This can take a while for a long document.")
    ok, message = voices.save_to_wav(text, target, voice, rate)
    if not ok:
        raise ValueError(message)
    job.log(message)


def _save_in_pieces(job: jobs.Job, sentences: list, target: Path, voice: str, rate: int) -> None:
    pieces = reading.chunk_for_recording(sentences)
    scratch = store.scratch_dir() / ("recording-" + job.id)
    scratch.mkdir(parents=True, exist_ok=True)
    job.log("Recording %d sentences in %d piece%s." %
            (len(sentences), len(pieces), "" if len(pieces) == 1 else "s"))
    parts = []
    try:
        for position, piece in enumerate(pieces, start=1):
            job.check()
            part = scratch / ("part-%03d.wav" % position)
            ok, message = voices.save_to_wav(piece, part, voice, rate)
            if not ok:
                raise ValueError(message)
            parts.append(part)
            job.set_progress(int(position * 100 / len(pieces)) - 1)
            job.log("Recorded %d of %d." % (position, len(pieces)))

        if len(parts) == 1:
            os.replace(str(parts[0]), str(target))
        else:
            job.log("Joining the pieces into one recording…")
            if not voices.join_wavs(parts, target):
                raise ValueError("The pieces recorded, but could not be joined into one file.")
        job.set_progress(99)
    finally:
        for part in parts:
            try:
                part.unlink()
            except OSError:
                pass
        try:
            scratch.rmdir()
        except OSError:
            pass


def make_recording(title: str, sentences: list, voice: str, rate: int, book: jobs.JobBook) -> jobs.Job:
    """Record a reading into a file you can keep."""

    def work(job: jobs.Job) -> None:
        current = voices.engine()
        if current is None:
            raise ValueError("There is no voice on this computer to record with.")
        if not getattr(current, "writes_wav", False):
            raise ValueError("%s can read out loud but cannot save a recording. "
                             "Playing it aloud still works." % current.label)

        target = store.free_name("%s.wav" % reading.title_for(title))
        job.log("Recording “%s” with %s." % (title, voice or "the usual voice"))
        if getattr(current, "chunked_save", False):
            _save_in_pieces(job, sentences, target, voice, rate)
        else:
            _save_in_one_go(job, " ".join(sentences), target, voice, rate)

        size = voices.file_size_mb(target)
        job.finish({
            "name": target.name,
            "download": "/api/download/" + target.name,
            "size_mb": size,
            "seconds": int(job.ended - job.started),
            "sentence": "Recorded %d sentences — %.1f MB." % (len(sentences), size),
            "note": ("This is a WAV file, so it is larger than an MP3 would be. nanoWrap "
                     "(the sibling app) can turn it into an MP3 if you have FFmpeg."),
        })
        store.remember({"title": title, "audio": str(target), "audio_name": target.name,
                        "sentences": len(sentences), "size_mb": size})

    return book.start(title, work)


def recent(book: jobs.JobBook) -> list:
    return book.recent(6)
