"""Getting words out of things, and cutting them into sentences.

Two jobs live here:

* **Opening things** — plain text, Markdown, HTML, Word documents, EPUBs and
  PDFs. The PDF reader is deliberately small: it handles the ordinary kind of
  PDF that is *words* rather than *a picture of words*, and says so plainly when
  it meets the other kind instead of reading out nonsense.
* **Cutting text into sentences** — because nanoSay reads one sentence at a
  time so the page can highlight what you are hearing. Splitting English into
  sentences is mostly easy and occasionally not: "Dr. Smith paid 3.50 at
  4 p.m." is one sentence, not four. The rules below are the small set that
  covers the cases people actually write.
"""

from __future__ import annotations

import html
import os
import re
import zipfile
import zlib
from pathlib import Path

# --------------------------------------------------------------------------
# What we can open, and what to say when we cannot
# --------------------------------------------------------------------------
PLAIN_SUFFIXES = (".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".log",
                  ".json", ".yaml", ".yml", ".ini", ".srt", ".vtt", ".text")
SUFFIXES = PLAIN_SUFFIXES + (".html", ".htm", ".xhtml", ".docx", ".epub", ".pdf", ".xml")


class Unreadable(ValueError):
    """Raised with a sentence a person can act on."""


def read_text_file(path: Path) -> tuple:
    """Read a text file without guessing wrong about its encoding.

    Real files from real computers are UTF-8, or UTF-8 with a byte-order mark,
    or the older Windows encoding. Trying them in that order covers essentially
    everything, and latin-1 always works as the last resort.
    """
    raw = Path(path).read_bytes()
    if raw.startswith(b"%PDF"):
        return "", "pdf"
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8 with unreadable characters"


def text_from_html(markup: str) -> str:
    """Take the words and leave the plumbing."""
    markup = re.sub(r"(?is)<(script|style|noscript|head)\b.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>", "\n", markup)
    markup = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section|article|blockquote)\s*>", "\n\n", markup)
    markup = re.sub(r"(?i)<li[^>]*>", "\n• ", markup)
    markup = re.sub(r"(?s)<[^>]+>", " ", markup)
    text = html.unescape(markup)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def text_from_docx(path: Path) -> str:
    """A .docx is a zip of XML. Paragraphs are <w:p>, breaks are <w:br>."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist()
                     if name.startswith("word/") and name.endswith(".xml")
                     and "document" in name]
            if not names:
                raise Unreadable("That Word file has no words inside it.")
            markup = archive.read(names[0]).decode("utf-8", errors="replace")
    except zipfile.BadZipFile:
        raise Unreadable("That .docx file is damaged, so nothing could be read from it.")
    markup = re.sub(r"(?i)<w:br\s*/?>", "\n", markup)
    markup = re.sub(r"(?i)</w:p>", "\n\n", markup)
    markup = re.sub(r"(?i)<w:tab\s*/?>", " ", markup)
    markup = re.sub(r"(?s)<[^>]+>", "", markup)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(markup)).strip()


def text_from_epub(path: Path) -> str:
    """An .epub is a zip of web pages."""
    try:
        with zipfile.ZipFile(path) as archive:
            pages = sorted(name for name in archive.namelist()
                           if name.lower().endswith((".xhtml", ".html", ".htm")))
            if not pages:
                raise Unreadable("That e-book has no readable pages inside it.")
            pieces = []
            for name in pages:
                markup = archive.read(name).decode("utf-8", errors="replace")
                pieces.append(text_from_html(markup))
    except zipfile.BadZipFile:
        raise Unreadable("That e-book file is damaged, so nothing could be read from it.")
    return "\n\n".join(piece for piece in pieces if piece.strip())


# --------------------------------------------------------------------------
# PDFs — the ordinary kind
# --------------------------------------------------------------------------
_PDF_STRING = re.compile(rb"\((?:\\.|[^\\()])*\)")
_PDF_HEX = re.compile(rb"<([0-9A-Fa-f\s]{2,})>")
_PDF_OPERATORS = re.compile(rb"(\[(?:[^\[\]\\]|\\.)*\]\s*TJ)|"
                            rb"(\((?:[^()\\]|\\.)*\)\s*(?:Tj|'|\"))|"
                            rb"([T][*dD])|([E][T])|"
                            rb"(BT)|(TD)|(Td)")
_PDF_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b",
                b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}


def _unescape_pdf_string(raw: bytes) -> bytes:
    out = bytearray()
    index = 0
    while index < len(raw):
        char = raw[index:index + 1]
        if char != b"\\":
            out += char
            index += 1
            continue
        following = raw[index + 1:index + 2]
        if following in _PDF_ESCAPES:
            out += _PDF_ESCAPES[following]
            index += 2
        elif following.isdigit():
            digits = raw[index + 1:index + 4]
            taken = 0
            while taken < len(digits) and digits[taken:taken + 1].isdigit() and taken < 3:
                taken += 1
            try:
                out += bytes([int(digits[:taken], 8) & 0xFF])
            except ValueError:
                pass
            index += 1 + taken
        elif following in (b"\n", b"\r"):
            index += 2
        else:
            out += following
            index += 2
    return bytes(out)


def _streams(data: bytes):
    """Every stream in the file, decompressed when it is compressed."""
    for found in re.finditer(rb"stream\r?\n", data):
        start = found.end()
        end = data.find(b"endstream", start)
        if end < 0:
            continue
        header = data[max(0, found.start() - 400):found.start()]
        body = data[start:end]
        if b"FlateDecode" in header:
            try:
                yield zlib.decompress(body.rstrip(b"\r\n"))
                continue
            except zlib.error:
                try:
                    yield zlib.decompressobj().decompress(body)
                    continue
                except zlib.error:
                    continue
        yield body


def pdf_text(data: bytes) -> str:
    """Pull the words out of an ordinary PDF.

    No fonts are decoded, so a PDF that uses a custom font encoding can come out
    looking like nonsense. That is what :func:`looks_like_nonsense` is for.
    """
    pieces = []
    for stream in _streams(data):
        if b"Tj" not in stream and b"TJ" not in stream:
            continue
        for found in _PDF_OPERATORS.finditer(stream):
            if found.group(1) or found.group(2):
                chunk = found.group(0)
                for text in _PDF_STRING.finditer(chunk):
                    pieces.append(_unescape_pdf_string(text.group(0)[1:-1]))
                for text in _PDF_HEX.finditer(chunk):
                    digits = re.sub(rb"\s+", b"", text.group(1))
                    if len(digits) % 2:
                        digits += b"0"
                    try:
                        pieces.append(bytes.fromhex(digits.decode("ascii")))
                    except ValueError:
                        continue
                pieces.append(b" ")
            elif found.group(5) or found.group(3) or found.group(4):
                pieces.append(b"\n")
        pieces.append(b"\n\n")
    text = b"".join(pieces).decode("latin-1")
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def looks_like_nonsense(text: str) -> bool:
    """True when what came out of a document is not really words.

    A scanned PDF yields nothing at all; a PDF with an unusual font encoding
    yields things like "ǼȸĜŊ" — a lot of unusual characters and very few of the
    small familiar words that every page of prose has.
    """
    stripped = text.strip()
    if len(stripped) < 8:
        return True
    letters = sum(1 for char in stripped if char.isalnum())
    if letters == 0:
        return True
    if len(stripped) > 200 and letters < len(stripped) * 0.4:
        return True
    odd = sum(1 for char in stripped if ord(char) > 0x2019)
    if odd > len(stripped) * 0.05:
        return True
    common = (" the ", " and ", " of ", " to ", " a ", " in ", " is ", " it ")
    return len(stripped) > 600 and not any(word in stripped.lower() for word in common)


# Suffixes that are definitely not words. Saying so plainly beats trying to
# read a photograph as if it were a letter.
NOT_WORDS = {
    "picture": (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic", ".svg", ".ico"),
    "video": (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".mpg", ".mpeg"),
    "sound": (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"),
    "packed-up file": (".zip", ".7z", ".rar", ".tar", ".gz", ".xz", ".bz2"),
    "program": (".exe", ".dll", ".msi", ".app", ".apk", ".deb", ".dmg"),
}

NOT_WORDS_LOOKUP = {suffix: word for word, suffixes in NOT_WORDS.items() for suffix in suffixes}


def looks_like_text(text: str) -> bool:
    """True when a file we do not recognise still holds ordinary writing."""
    if "\x00" in text:
        return False
    sample = text[:4000]
    letters = sum(1 for char in sample if char.isalpha())
    printable = sum(1 for char in sample if char.isprintable() or char in "\n\r\t")
    return letters >= 20 and printable > len(sample) * 0.9


# --------------------------------------------------------------------------
# Opening anything
# --------------------------------------------------------------------------
def extract(path) -> tuple:
    """Return ``(text, note)`` for a file. Raises Unreadable with a reason."""
    path = Path(path)
    if not path.is_file():
        raise Unreadable("I cannot find that file any more. Please drop it in again.")
    suffix = path.suffix.lower()

    if suffix in PLAIN_SUFFIXES:
        text, encoding = read_text_file(path)
        if suffix in (".html", ".htm", ".xhtml"):
            text = text_from_html(text)
        return text, "Read as text (%s)." % encoding

    if suffix in (".html", ".htm", ".xhtml", ".xml"):
        text, encoding = read_text_file(path)
        return text_from_html(text), "Read as a web page (%s)." % encoding

    if suffix == ".docx":
        return text_from_docx(path), "Read as a Word document."

    if suffix == ".epub":
        return text_from_epub(path), "Read as an e-book."

    if suffix == ".pdf":
        data = path.read_bytes()
        text = pdf_text(data)
        if looks_like_nonsense(text):
            raise Unreadable(
                "I could not find real words in this PDF. It is most likely a scan — a "
                "picture of pages rather than text — or it uses an unusual font that "
                "nanoSay cannot undo. A PDF that was made from a document rather than "
                "scanned will work.")
        pages = max(1, len(re.findall(rb"/Type\s*/Page[^s]", data)))
        return text, "Read the words out of a %d-page PDF." % pages

    if suffix in (".doc", ".rtf", ".odt", ".pages"):
        raise Unreadable(
            "nanoSay can read .docx, PDFs and plain text, but not %s files. Open it in "
            "Word (or Google Docs) and save a copy as .docx — that works straight away."
            % suffix)

    if suffix in NOT_WORDS_LOOKUP:
        raise Unreadable(
            "That is a %s, not words. nanoSay reads PDFs, Word documents, e-books, "
            "web pages and plain text." % NOT_WORDS_LOOKUP[suffix])

    # Last resort: an unfamiliar name, but it might still be ordinary writing.
    text, encoding = read_text_file(path)
    if looks_like_text(text):
        return text, "It had an unfamiliar name, but there was readable text inside (%s)." % encoding
    raise Unreadable("I do not know how to read a %s file." % (suffix or "file like that"))


# --------------------------------------------------------------------------
# Cutting text into sentences
# --------------------------------------------------------------------------
# A full stop after one of these is not the end of a sentence.
ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "vs", "etc", "e.g", "i.e",
    "no", "fig", "approx", "dept", "est", "inc", "ltd", "co", "min", "max",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
    "vol", "pp", "ch", "sec", "al", "cf", "ed", "eds", "trans", "univ", "assn",
}

_END = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s|$)")
_SPACE = re.compile(r"[ \t\u00a0\f\v]+")
_LONG = 420


def _is_real_end(text: str, match: re.Match) -> bool:
    """Is this full stop (or question mark) the end of a sentence?"""
    before = text[:match.start()].rstrip()
    after = text[match.end():].lstrip()
    if not after:
        return True

    # 3.14, 1,234.56, section 2.3
    if before[-1:].isdigit() and after[:1].isdigit():
        return False
    token = re.split(r"[\s(\[{]", before)[-1].lower().strip("([\"'“”")
    if token in ABBREVIATIONS:
        return False
    # A single letter is an initial: "J. K. Rowling"
    if len(token) == 1 and token.isalpha():
        return False
    # A lower-case next word means the sentence carried on — which is what
    # happens after a line of dialogue: “Are you coming?” and he said nothing.
    if after[:1].islower() and not after.startswith(("i ", "i'", "i,")):
        return False
    return True


def split_sentences(text: str) -> list:
    """Cut text into the pieces a reader would say one after another."""
    if not text:
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out: list = []
    for block in text.split("\n"):
        line = _SPACE.sub(" ", block).strip()
        if not line:
            continue
        start = 0
        for match in _END.finditer(line):
            if not _is_real_end(line, match):
                continue
            piece = line[start:match.end()].strip()
            if piece:
                out.append(piece)
            start = match.end()
        tail = line[start:].strip()
        if tail:
            out.append(tail)

    # A very long sentence is hard to follow on screen; break it at the
    # punctuation that marks a natural breath.
    result: list = []
    for sentence in out:
        result.extend(_break_up(sentence))
    return [sentence for sentence in result if any(char.isalnum() for char in sentence)]


def _break_up(sentence: str) -> list:
    if len(sentence) <= _LONG:
        return [sentence]
    pieces = re.split(r"(?<=[;:])\s+", sentence)
    out: list = []
    current = ""
    for piece in pieces:
        candidate = (current + " " + piece).strip()
        if len(candidate) > _LONG and current:
            out.append(current)
            current = piece
        else:
            current = candidate
    if current:
        out.append(current)
    # Still too long: fall back to commas.
    final: list = []
    for piece in out:
        if len(piece) <= _LONG * 1.6:
            final.append(piece)
            continue
        parts = re.split(r"(?<=,)\s+", piece)
        current = ""
        for part in parts:
            candidate = (current + " " + part).strip()
            if len(candidate) > _LONG and current:
                final.append(current)
                current = part
            else:
                current = candidate
        if current:
            final.append(current)
    return final


def chunk_for_recording(sentences: list, size: int = 1600) -> list:
    """Group sentences into pieces of about `size` characters.

    Used when recording: one file per piece means the progress bar can tell the
    truth, and the pieces are joined at the end.
    """
    pieces: list = []
    current: list = []
    length = 0
    for sentence in sentences:
        if current and length + len(sentence) > size:
            pieces.append(" ".join(current))
            current, length = [], 0
        current.append(sentence)
        length += len(sentence) + 1
    if current:
        pieces.append(" ".join(current))
    return pieces


def words(text: str) -> int:
    return len(re.findall(r"[^\s]+", text or ""))


def estimate(sentences: list, rate: int = 0) -> dict:
    """How long this will take to listen to, in plain words."""
    total_words = sum(words(sentence) for sentence in sentences)
    # SAPI notches: every notch is roughly 15% faster or slower than 175 wpm.
    per_minute = 175 * (1 + 0.15 * float(rate or 0))
    per_minute = max(60.0, per_minute)
    seconds = int(total_words * 60 / per_minute)
    if seconds < 60:
        spoken = "%d seconds" % max(5, seconds)
    elif seconds < 5400:
        spoken = "%d minutes" % round(seconds / 60)
    else:
        hours = seconds // 3600
        spoken = "%d hours %d minutes" % (hours, round((seconds % 3600) / 60))
    return {"words": total_words, "sentences": len(sentences), "seconds": seconds, "spoken": spoken}


def tidy_for_speaking(text: str) -> str:
    """Small fixes that stop a voice reading punctuation out loud."""
    text = re.sub(r"https?://\S+", "the link", text)
    text = text.replace("&", " and ")
    text = re.sub(r"\s*[-–—]{2,}\s*", " — ", text)
    text = re.sub(r"[•·]\s*", "", text)
    text = re.sub(r"\*+", "", text)
    text = re.sub(r"(?<=[a-z])#(?=\s|$)", "", text)
    return text


def title_for(path) -> str:
    """A friendly name for a reading: the file name without the plumbing."""
    stem = Path(path).stem if path else "reading"
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    stem = re.sub(r"\s{2,}", " ", stem)
    return (stem[:60] or "reading")


def folder_note(path) -> str:
    """Where the file came from, for the page to show under the title."""
    parent = os.path.dirname(str(path))
    return parent
