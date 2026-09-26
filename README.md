<div align="center">

# nanoSay

**Have anything read out loud, by the voice already in your computer.**
No account. No internet. No voice to download.

Drop in a PDF, a Word file or a web page — or paste anything at all — and your
computer reads it to you, highlighting each sentence as it is spoken, and can
save the whole thing as a recording you can play anywhere.

[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9+-58a6ff.svg)]()
[![dependencies](https://img.shields.io/badge/required%20deps-0-f0883e.svg)]()
[![tests](https://img.shields.io/badge/tests-66%20passing-3ddc97.svg)]()

</div>

---

> **Part of [the nano family](https://github.com/Agarwalrishu13/nano)** — eleven offline-first apps for people who do not code. This is the map of the whole project.


## What this is, in one paragraph

Your operating system has had a voice in it for twenty years. Windows has the
voices under *Settings → Time & language → Speech*; macOS has `say`; Linux has
eSpeak. Almost nobody uses them, because reaching them means typing a command
and reading a manual. nanoSay is the front door: it finds the voice that is
already there, takes the words out of almost anything you give it, cuts them into
sentences, and reads them one at a time so the page can follow along and highlight
what you are hearing. Then, if you want to keep it, it writes the whole thing into
a `.wav` file — no account, no upload, no queue, no character limit, and nothing
installed.

---

## Use it

1. Install Python if you do not have it — [python.org/downloads](https://www.python.org/downloads/).
2. Download this repo and unzip it.
3. **Windows:** double-click `run.bat`. **macOS / Linux:** `./run.sh`.
4. Your browser opens at `http://127.0.0.1:8766`.

Nothing to hand? Three examples come with the app, including one built out of the
sentences that trip readers up — *"Dr. Smith paid 3.50 for the tickets on 4 p.m.
on Tue. 12 Mar."* is one sentence, not four, and it is read that way.

<details>
<summary>Prefer the command line? (you do not need to)</summary>

```bash
python start.py                        # start and open the browser
python -m nanosay doctor               # list the voices on this computer
python -m nanosay --port 9000 --no-browser
```

</details>

---

## The four steps it walks you through

| step | what happens |
|---|---|
| **1. Give it something** | Drop a file, paste words, or give it a web address. Text, Markdown, HTML, CSV, Word (`.docx`), EPUB and PDF all work. |
| **2. Choose a voice** | The voices on *this* computer, with the speed control in the units that voice actually uses. Windows: ten notches either way. macOS and eSpeak: words a minute. |
| **3. Listen** | The sentence being spoken is highlighted and the page scrolls to keep up. **Pause** (which carries on from the same sentence, not the middle of a word), **Next**, and **Stop**. Clicking any sentence starts from there. |
| **4. Keep a recording** | Press **Record it as a file** and the whole thing is written to a `.wav` you can download, put on a phone, or send to somebody. Recording runs faster than listening does. |

### Things it does that you would not expect from a toy

- **It reads sentence by sentence, on purpose.** That is what makes the
  highlighting work. On Windows the whole reading is handed to one PowerShell
  process which announces each sentence number as it reaches it, so there is no
  pause between sentences while a program starts up.
- **Your words are never put inside a command.** They are written to a small
  text file and the voice engine is told to read that file. A document full of
  quotes, semicolons and `$(...)` is just words — which is not a small thing when
  the thing doing the reading is PowerShell. There is a test that proves it.
- **It knows where sentences really end.** Abbreviations (`Dr.`, `e.g.`, `p. 29`,
  `Jan.`), decimals (`3.50`), initials (`J. K. Rowling`) and dialogue
  (`"Are you coming?" and he said nothing`) are handled. Fourteen tests are
  devoted to this, because getting it wrong is the difference between a voice
  that sounds like a person and one that sounds like a machine reading punctuation.
- **A scan is refused, not faked.** Most PDFs are words; some are photographs of
  words. nanoSay reads the first kind and, for the second, says *"this is most
  likely a scan"* instead of reading out an empty room.
- **It can ask the AI next door for a short version.** If you have
  [nanoLaama](https://github.com/Agarwalrishu13/nanolaama) running, one button
  gets a two-minute version of a forty-page document before it is read — and if
  nanoLaama is not running, the app says so in one sentence and carries on.
- **It tidies up after itself.** Recordings are cleared out after a week, because
  a WAV of a long document is a big file.

---

## What it does not do, honestly

- **No OCR.** A scanned PDF has pictures of words in it, and getting those back
  needs an OCR program. nanoSay tells you when that is what you have given it.
- **Some PDFs come out garbled.** PDFs made with an unusual embedded font
  encoding can produce nonsense, because decoding fonts is a very large job.
  Ordinary PDFs — the kind printed from Word, a browser or LaTeX — are fine.
- **`.doc`, `.rtf` and `.odt` are not read.** Open it in Word or Google Docs and
  save a copy as `.docx`; that works immediately, and the app says exactly this.
- **The recordings are WAV, not MP3.** WAV is uncompressed, so the files are
  large — about 10 MB for ten minutes. There is no MP3 encoder inside Python.
  ([nanoWrap](https://github.com/Agarwalrishu13/nanowrap) will turn it into an
  MP3 for you if FFmpeg is installed.)
- **It does not download or install a voice.** It uses what is there. The quality
  of the voice is the quality of your operating system's voice, which on Linux
  means eSpeak, which sounds like eSpeak.
- **It does not listen to you.** Speaking *to* the computer is a different
  problem with much worse built-in support; nanoSay only reads.

---

## Where your files live

```
~/.nanosay/
  given/         documents you dropped in (copies — deleted when nanoSay closes)
  recordings/    the .wav files you asked it to make
  working/       pieces of a recording, before they are joined up
  library.json   what you have read before
  settings.json
```

Delete that folder and nanoSay forgets everything. Nothing is uploaded. The only
thing it ever contacts is the address you set for nanoLaama — and that address
defaults to `127.0.0.1`, this machine.

---

## The rest of the family

| app | what it is for |
|---|---|
| 🧭 [nanoHome](https://github.com/Agarwalrishu13/nanohome) | one front door for every nano app on this computer |
| 🧠 [nanoLaama](https://github.com/Agarwalrishu13/nanolaama) | talk to an AI on your own computer, offline |
| 📚 [nanoDoc](https://github.com/Agarwalrishu13/nanodoc) | drop in a document, ask it anything |
| 📊 [nanoLearn](https://github.com/Agarwalrishu13/nanolearn) | drop a spreadsheet, get an answer machine |
| 🔊 [**nanoSay**](https://github.com/Agarwalrishu13/nanosay) | have anything read out loud — *this repo* |
| 🧲 [nanoPick](https://github.com/Agarwalrishu13/nanopick) | find your files by saying what you remember |
| 🎵 [nanoTune](https://github.com/Agarwalrishu13/nanotune) | your music, one page, no account |
| 🧰 [nanoWrap](https://github.com/Agarwalrishu13/nanowrap) | the best-known programs, with ready-made buttons |
| ⌨️ [nanoShell](https://github.com/Agarwalrishu13/nanoshell) | any program at all, with words instead of flags |
| 🗂 [nanoGit](https://github.com/Agarwalrishu13/nanogit) | your folder, kept safe without learning git |
| 🖥 [nanoDesk](https://github.com/Agarwalrishu13/nanodesk) | every nano-style app you have, one click away |
| 🃏 [nonoForge](https://github.com/Agarwalrishu13/nonoforge) | pick a card, press one button, you have an app |

And underneath them, for people who want to see the gears: [nanollama.c](https://github.com/Agarwalrishu13/nanollama.c) (the C engine), [nanobrain](https://github.com/Agarwalrishu13/nanobrain) (training from scratch), [nanoforge](https://github.com/Agarwalrishu13/nanoforge) (the model studio) and [nanorl](https://github.com/Agarwalrishu13/nanorl) (alignment).

The map of the whole project — what each app is for, and how they fit together — lives in [the nano family](https://github.com/Agarwalrishu13/nano).
## Tests

```bash
python -m unittest discover tests -v
```

66 tests, and none of them make a sound. The voice itself is only touched where
it writes to a file — the one thing a speech engine can do in complete silence.
On a machine with a voice, two tests really do record a sentence and check the
result is a playable WAV. The rest cover the parts that decide what gets read:
sentence endings, PDF internals (built by hand in the tests, compressed and not),
Word files, e-books, encodings, and the read-along state machine.

MIT licensed. No dependencies. `python start.py` is the whole install.
