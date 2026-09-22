"""The voices already inside this computer.

There is no speech engine in this file and nothing to install. Every operating
system ships one, and this module is the adapter that finds it, asks it what
voices it has, and drives it:

===========  ===========================================================
Windows      ``System.Speech`` (the voices under "Speech" in Settings),
             driven through PowerShell; ``SAPI.SpVoice`` if that is missing
macOS        ``say``
Linux        ``espeak-ng`` or ``espeak``, or ``spd-say`` to speak only
===========  ===========================================================

Two rules are kept throughout:

* **Your text is never put inside a command.** It is written to a small text
  file and the voice engine is told to read that file. So a document containing
  quotes, semicolons, ``$(...)`` or anything else is just words, and can never
  become a command by accident.
* **Nothing is spoken until you press play.** Listing voices and probing what
  this computer can do happens silently.

Long text is read one sentence at a time so the page can highlight the sentence
you are hearing. On Windows the whole reading is handed to one PowerShell
process which announces each sentence number as it reaches it — that keeps it
smooth, with no pause between sentences while a program starts up.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _hide_console() -> int:
    """Windows would otherwise flash a black window for every sentence."""
    return CREATE_NO_WINDOW


def _run(argv: list, timeout: int = 30) -> tuple:
    """Run something small and quiet (listing voices, probing). (code, text)."""
    try:
        finished = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, text=True, errors="replace",
            creationflags=_hide_console(),
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return finished.returncode, finished.stdout or ""


# ==========================================================================
# Windows: System.Speech, and SAPI.SpVoice as a fallback
# ==========================================================================
# Sentence numbers are announced on standard output so the page knows exactly
# which sentence is being spoken. Your words are read from the queue file.
_PS_VOICES = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$s.GetInstalledVoices() | ForEach-Object { "
    "$i = $_.VoiceInfo; "
    "Write-Output ($i.Name + [char]9 + $i.Gender + [char]9 + $i.Culture) }; "
    "$s.Dispose()"
)

_PS_SPEAK_QUEUE = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = '__VOICE__'
if ($voice -ne '') { try { $s.SelectVoice($voice) } catch { } }
$s.Rate = __RATE__
$s.Volume = __VOLUME__
$lines = @(Get-Content -LiteralPath '__QUEUE__' -Encoding UTF8)
for ($i = 0; $i -lt $lines.Count; $i++) {
  $line = [string]$lines[$i]
  if ($line.Trim().Length -eq 0) { continue }
  [Console]::Out.WriteLine('NANO ' + $i)
  [Console]::Out.Flush()
  $s.Speak($line)
}
[Console]::Out.WriteLine('NANO done')
$s.Dispose()
"""

_PS_SAVE = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = '__VOICE__'
if ($voice -ne '') { try { $s.SelectVoice($voice) } catch { } }
$s.Rate = __RATE__
$text = Get-Content -Raw -LiteralPath '__QUEUE__' -Encoding UTF8
$s.SetOutputToWaveFile('__OUT__')
$s.Speak($text)
$s.Dispose()
Write-Output 'NANO saved'
"""

# Used only when System.Speech cannot be loaded (some PowerShell 7 installations).
_COM_VOICES = (
    "$v = New-Object -ComObject SAPI.SpVoice; "
    "$v.GetVoices() | ForEach-Object { Write-Output $_.GetDescription() }"
)
_COM_SPEAK_QUEUE = r"""
$ErrorActionPreference = 'Stop'
$v = New-Object -ComObject SAPI.SpVoice
$v.Rate = __RATE__
$v.Volume = __VOLUME__
$lines = @(Get-Content -LiteralPath '__QUEUE__' -Encoding UTF8)
for ($i = 0; $i -lt $lines.Count; $i++) {
  $line = [string]$lines[$i]
  if ($line.Trim().Length -eq 0) { continue }
  [Console]::Out.WriteLine('NANO ' + $i)
  [Console]::Out.Flush()
  $v.Speak($line) | Out-Null
}
[Console]::Out.WriteLine('NANO done')
"""


class WindowsVoices:
    """The voices that come with Windows."""

    id = "windows"
    label = "Windows voices"
    queue_mode = True          # one process reads the lot and counts sentences
    writes_wav = True
    chunked_save = True        # record in pieces, so progress is real
    supports_volume = True

    def __init__(self) -> None:
        self._shell = ""
        self._com = False
        self._voices: list | None = None
        self._lock = threading.Lock()

    # -- finding it ------------------------------------------------------
    def _find_shell(self) -> str:
        # Windows PowerShell first: System.Speech is part of it. PowerShell 7 is
        # tried after, because it does not always carry the assembly.
        for name in ("powershell.exe", "powershell", "pwsh"):
            found = shutil.which(name)
            if found:
                return found
        return ""

    def available(self) -> bool:
        with self._lock:
            if self._voices is not None:
                return bool(self._voices)
            self._shell = self._find_shell()
            if not self._shell:
                self._voices = []
                return False
            code, text = _run([self._shell, "-NoProfile", "-NonInteractive", "-Command", _PS_VOICES], timeout=40)
            voices = self._parse_speech(text)
            if not voices:
                code, text = _run([self._shell, "-NoProfile", "-NonInteractive", "-Command", _COM_VOICES], timeout=40)
                described = [line.strip() for line in text.splitlines() if line.strip()]
                voices = [{"id": name, "name": name.split(" - ")[0].strip(), "gender": "", "language": ""}
                          for name in described]
                self._com = bool(voices)
            self._voices = voices
            return bool(voices)

    @staticmethod
    def _parse_speech(text: str) -> list:
        voices = []
        for line in (text or "").splitlines():
            parts = [part.strip() for part in line.split("\t")]
            if len(parts) >= 3 and parts[0]:
                voices.append({"id": parts[0], "name": parts[0],
                               "gender": parts[1], "language": parts[2]})
        return voices

    def voices(self) -> list:
        if not self.available():
            return []
        return list(self._voices or [])

    # -- driving it ------------------------------------------------------
    def note(self) -> str:
        if self._com:
            return ("Windows speech voices (driven the older way, because the newer "
                    "interface was not available).")
        return ("The voices that come with Windows — the same ones under "
                "Settings → Time & language → Speech.")

    def rate_range(self) -> dict:
        return {"min": -10, "max": 10, "default": 0, "unit": "notches, 0 is normal"}

    def _quote(self, value: str) -> str:
        """A PowerShell single-quoted string: the only escape needed is doubling."""
        return "'" + str(value).replace("'", "''") + "'"

    def queue_command(self, queue: Path, voice: str, rate: int, volume: int) -> list:
        if self._com:
            script = (_COM_SPEAK_QUEUE
                      .replace("__RATE__", str(int(rate)))
                      .replace("__VOLUME__", str(int(volume)))
                      .replace("__QUEUE__", self._quote(str(queue))[1:-1]))
        else:
            script = (_PS_SPEAK_QUEUE
                      .replace("__VOICE__", self._quote(voice)[1:-1])
                      .replace("__RATE__", str(int(rate)))
                      .replace("__VOLUME__", str(int(volume)))
                      .replace("__QUEUE__", self._quote(str(queue))[1:-1]))
        return [self._shell, "-NoProfile", "-NonInteractive", "-Command", script]

    def save_command(self, queue: Path, out_path: Path, voice: str, rate: int) -> list:
        script = (_PS_SAVE
                  .replace("__VOICE__", self._quote(voice)[1:-1])
                  .replace("__RATE__", str(int(rate)))
                  .replace("__QUEUE__", self._quote(str(queue))[1:-1])
                  .replace("__OUT__", self._quote(str(out_path))[1:-1]))
        return [self._shell, "-NoProfile", "-NonInteractive", "-Command", script]

# ==========================================================================
# macOS: say
# ==========================================================================
class MacVoices:
    id = "macos"
    label = "macOS voices"
    queue_mode = False         # say starts instantly, so one call per sentence
    writes_wav = True
    chunked_save = False       # say writes the whole reading in one go
    supports_volume = False

    def __init__(self) -> None:
        self._path = shutil.which("say") or ""
        self._voices: list | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(self._path)

    def voices(self) -> list:
        with self._lock:
            if self._voices is not None:
                return list(self._voices)
            self._voices = []
            if not self._path:
                return []
            _code, text = _run([self._path, "-v", "?"])
            for line in (text or "").splitlines():
                # "Alex                en_US    # Most people recognize me by my voice."
                line = line.split("#")[0].rstrip()
                parts = line.split()
                if len(parts) < 2:
                    continue
                name, locale = parts[0], parts[-1]
                if not re.match(r"^[a-z]{2}(_[A-Z]{2})?$", locale or ""):
                    continue
                self._voices.append({"id": name, "name": name, "gender": "", "language": locale})
            return list(self._voices)

    def note(self) -> str:
        return ("The voices that come with macOS — the same list as "
                "System Settings → Accessibility → Spoken Content.")

    def rate_range(self) -> dict:
        return {"min": 90, "max": 400, "default": 175, "unit": "words a minute"}

    def speak_command(self, sentence: str, voice: str, rate: int, volume: int) -> list:
        command = [self._path]
        if voice:
            command += ["-v", voice]
        command += ["-r", str(int(rate)), "--", sentence]
        return command

    def save_command(self, queue: Path, out_path: Path, voice: str, rate: int) -> list:
        command = [self._path]
        if voice:
            command += ["-v", voice]
        command += ["-r", str(int(rate)), "--data-format=LEI16@22050", "-o", str(out_path), "-f", str(queue)]
        return command


# ==========================================================================
# Linux: espeak-ng, espeak, or spd-say
# ==========================================================================
class EspeakVoices:
    id = "espeak"
    label = "eSpeak voices"
    queue_mode = False
    writes_wav = True
    chunked_save = True
    supports_volume = False

    def __init__(self) -> None:
        self._path = shutil.which("espeak-ng") or shutil.which("espeak") or ""
        self._voices: list | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(self._path)

    def voices(self) -> list:
        with self._lock:
            if self._voices is not None:
                return list(self._voices)
            self._voices = []
            if not self._path:
                return []
            _code, text = _run([self._path, "--voices"])
            for line in (text or "").splitlines():
                parts = line.split()
                if len(parts) >= 4 and parts[0].isdigit():
                    # " 5  en            M  en-us          en-us   English (America)"
                    self._voices.append({"id": parts[3], "name": parts[3],
                                         "gender": {"M": "Male", "F": "Female"}.get(parts[2], ""),
                                         "language": parts[1]})
            return list(self._voices)

    def note(self) -> str:
        return ("eSpeak, which is on most Linux machines. It sounds robotic, and it is "
                "the honest price of working with nothing installed.")

    def rate_range(self) -> dict:
        return {"min": 80, "max": 450, "default": 175, "unit": "words a minute"}


class SpdVoices:
    """spd-say can speak but cannot write a file — last resort."""

    id = "spd"
    label = "speech-dispatcher"
    queue_mode = False
    writes_wav = False
    chunked_save = False
    supports_volume = False

    def __init__(self) -> None:
        self._path = shutil.which("spd-say") or ""

    def available(self) -> bool:
        return bool(self._path)

    def voices(self) -> list:
        if self._path:
            return [{"id": "", "name": "The system's own voice", "gender": "", "language": ""}]
        return []

    def note(self) -> str:
        return "speech-dispatcher can read to you, but cannot save a recording."

    def rate_range(self) -> dict:
        return {"min": -100, "max": 100, "default": 0, "unit": "notches, 0 is normal"}

    def speak_command(self, sentence: str, voice: str, rate: int, volume: int) -> list:
        return [self._path, "--wait", sentence]


# ==========================================================================
# Picking one
# ==========================================================================
_engine = None
_engine_lock = threading.Lock()


def engine():
    """The speech engine this computer has, or None with a reason."""
    global _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        candidates = []
        if sys.platform.startswith("win"):
            candidates = [WindowsVoices()]
        elif sys.platform == "darwin":
            candidates = [MacVoices()]
        else:
            candidates = [EspeakVoices(), SpdVoices()]
        for candidate in candidates:
            try:
                if candidate.available():
                    _engine = candidate
                    return _engine
            except Exception:
                continue
        _engine = None
        return _engine


def refresh():
    """Look again — for somebody who has just installed a voice engine."""
    global _engine
    with _engine_lock:
        _engine = None
    return engine()


def describe() -> dict:
    """What this computer can do, in the shape the page expects."""
    current = engine()
    if current is None:
        return {
            "available": False,
            "engine": "",
            "label": "",
            "voices": [],
            "note": _NO_ENGINE,
            "supports_volume": False,
            "can_save": False,
            "rate": {"min": 80, "max": 400, "default": 175, "unit": "words a minute"},
        }
    try:
        voices = current.voices()
    except Exception:
        voices = []
    return {
        "available": bool(voices) or bool(getattr(current, "queue_mode", False)),
        "engine": current.id,
        "label": current.label,
        "voices": voices,
        "note": current.note(),
        "supports_volume": bool(getattr(current, "supports_volume", False)),
        "can_save": bool(getattr(current, "writes_wav", False)),
        "chunked_save": bool(getattr(current, "chunked_save", False)),
        "rate": current.rate_range() if hasattr(current, "rate_range") else
                {"min": -10, "max": 10, "default": 0, "unit": "notches, 0 is normal"},
    }


_NO_ENGINE = (
    "This computer does not seem to have a voice installed. nanoSay needs one that is "
    "already there — it will not download one. On Linux, “sudo apt install espeak-ng” is "
    "usually enough; then press “Look again”."
)


# ==========================================================================
# Reading, sentence by sentence
# ==========================================================================
def open_reading(sentences: list, voice: str, rate: int, volume: int, queue: Path):
    """Start speaking, and hand back the process to watch.

    The process prints ``NANO <number>`` before each sentence so the page can
    follow along. Returns ``(process, on_line)``.
    """
    current = engine()
    if current is None:
        raise ValueError("There is no voice on this computer to read with.")

    if getattr(current, "queue_mode", False):
        queue.write_text("\n".join(sentences), encoding="utf-8")
        argv = current.queue_command(queue, voice, rate, volume)
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, bufsize=1, errors="replace",
            creationflags=_hide_console(),
        )
        return process, True

    raise ValueError("This voice engine reads one sentence at a time.")


def open_sentence(sentence: str, voice: str, rate: int, volume: int):
    """Speak a single sentence (used by engines that start instantly, and to
    carry on after a pause)."""
    current = engine()
    if current is None:
        raise ValueError("There is no voice on this computer to read with.")
    if not hasattr(current, "speak_command"):
        raise ValueError("This voice engine reads a whole page at a time, not a sentence.")
    argv = current.speak_command(sentence, voice, rate, volume)
    return subprocess.Popen(
        argv, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, errors="replace",
        creationflags=_hide_console(),
    )


def save_to_wav(text: str, target: Path, voice: str, rate: int) -> tuple:
    """Write the whole text to one .wav file. Returns (ok, message)."""
    current = engine()
    if current is None:
        return False, "There is no voice on this computer to record with."
    if not hasattr(current, "save_command"):
        return False, ("%s can read out loud, but cannot save a recording. "
                       "Reading aloud still works." % current.label)

    handle, tmp_name = tempfile.mkstemp(prefix="nanosay-save-", suffix=".txt")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(text)
    queue = Path(tmp_name)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        argv = current.save_command(queue, target, voice, rate)
        try:
            finished = subprocess.run(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=3600, text=True, errors="replace",
                creationflags=_hide_console(),
            )
        except subprocess.TimeoutExpired:
            return False, "Recording took far too long and was stopped."
        except OSError as exc:
            return False, "The voice engine would not start: %s" % exc
        if finished.returncode != 0 or not target.exists() or target.stat().st_size < 128:
            tail = [line for line in (finished.stdout or "").splitlines() if line.strip()][-2:]
            return False, ("The voice engine could not write the recording.%s"
                           % ((" It said: " + " / ".join(tail)) if tail else ""))
        return True, "Recorded %s" % target.name
    finally:
        try:
            queue.unlink()
        except OSError:
            pass


def join_wavs(parts: list, target) -> bool:
    """Join pieces of a recording into one file.

    Only used where the engine writes one file per piece. The pieces all come
    from the same voice at the same settings, so their formats match.
    """
    target = Path(target)
    usable = [Path(part) for part in parts if Path(part).is_file() and Path(part).stat().st_size > 44]
    if not usable:
        return False
    with wave.open(str(target), "wb") as out:
        settings = None
        for part in usable:
            try:
                with wave.open(str(part), "rb") as source:
                    if settings is None:
                        settings = source.getparams()
                        out.setparams(settings)
                    out.writeframes(source.readframes(source.getnframes()))
            except (wave.Error, OSError):
                continue
    return target.exists() and target.stat().st_size > 44


def file_size_mb(path) -> float:
    try:
        return round(Path(path).stat().st_size / (1024 * 1024), 2)
    except OSError:
        return 0.0


def voices_json() -> str:
    return json.dumps(describe(), default=str)
