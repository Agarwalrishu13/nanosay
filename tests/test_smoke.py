"""Tests for nanoSay.

Nothing here makes a sound. The voice itself is only touched where it writes to
a file — the one thing a speech engine can do in complete silence — and even
that is skipped on a machine with no voice engine at all.

The parts worth testing carefully are the ones that decide what gets read out
loud: sentences that end where a person thinks they end, and words that can be
got out of a PDF, a Word file or a web page.
"""

import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="nanosay-tests-")
os.environ["NANOSAY_HOME"] = _TMP

from nanosay import jobs, reading, reader, server, shorten, store, voices  # noqa: E402
from nanosay.httpbase import free_port  # noqa: E402
from nanosay.server import create_app  # noqa: E402


def _write(name, content, folder=None):
    path = os.path.join(folder or tempfile.mkdtemp(prefix="nanosay-in-"), name)
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(path, mode) as handle:
        handle.write(content)
    return path


# ==========================================================================
# Cutting text into sentences
# ==========================================================================
class TestSentences(unittest.TestCase):
    def test_ordinary_sentences(self):
        text = "The cat sat on the mat. The dog sat on the log! Was that a question?"
        self.assertEqual(reading.split_sentences(text), [
            "The cat sat on the mat.", "The dog sat on the log!",
            "Was that a question?",
        ])

    def test_abbreviations_do_not_end_a_sentence(self):
        cases = [
            "Dr. Patel arrived at 3 p.m. It was late.",
            "Mr. and Mrs. Sharma came to tea.",
            "See Fig. 4 and p. 29 for the numbers.",
            "It is cold in Jan. and Feb. every year.",
            "Bring pens, paper, etc. and a coat.",
        ]
        expected = [2, 1, 1, 1, 1]
        for text, count in zip(cases, expected):
            with self.subTest(text=text):
                self.assertEqual(len(reading.split_sentences(text)), count)

    def test_decimals_and_thousands_are_not_sentence_ends(self):
        self.assertEqual(len(reading.split_sentences("It cost 3.50 dollars. That is a lot.")), 2)
        self.assertEqual(len(reading.split_sentences("There were 1,234.56 of them. All gone.")), 2)

    def test_initials_keep_their_owner(self):
        self.assertEqual(len(reading.split_sentences("J. K. Rowling wrote it. She is famous.")), 2)
        self.assertEqual(len(reading.split_sentences("A. A. Milne wrote the bear.")), 1)

    def test_dialogue_carrying_on_is_one_sentence(self):
        text = "She said, “Wait — are you coming?” and he said nothing at all."
        self.assertEqual(len(reading.split_sentences(text)), 1)
        self.assertEqual(len(reading.split_sentences(text + " That silence lasted.")), 2)

    def test_question_marks_still_end_sentences(self):
        self.assertEqual(len(reading.split_sentences("Are you there? Yes I am.")), 2)

    def test_every_line_of_a_list_is_its_own_sentence(self):
        text = "Shopping list\nbread\nmilk\ncoffee"
        self.assertEqual(reading.split_sentences(text),
                         ["Shopping list", "bread", "milk", "coffee"])

    def test_a_very_long_sentence_is_broken_at_a_breath(self):
        long_one = ("The committee considered the proposal at length; " * 12).strip()
        pieces = reading.split_sentences(long_one)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(piece) < 700 for piece in pieces))

    def test_nothing_in_nothing_out(self):
        self.assertEqual(reading.split_sentences(""), [])
        self.assertEqual(reading.split_sentences("   \n\n  "), [])

    def test_a_sentence_with_no_words_is_dropped(self):
        self.assertEqual(reading.split_sentences("...\n---\nReal words here."), ["Real words here."])

    def test_recording_pieces_are_grouped_and_joined_back(self):
        sentences = ["Sentence number %d." % number for number in range(200)]
        pieces = reading.chunk_for_recording(sentences, size=500)
        self.assertGreater(len(pieces), 1)
        self.assertEqual(" ".join(pieces).split(), " ".join(sentences).split())
        self.assertTrue(all(len(piece) < 700 for piece in pieces))

    def test_the_time_estimate_reads_like_a_person(self):
        sentences = reading.split_sentences("word " * 1200)
        estimate = reading.estimate(sentences, 0)
        self.assertIn("minutes", estimate["spoken"])
        self.assertEqual(estimate["words"], 1200)
        slower = reading.estimate(sentences, -5)
        self.assertGreater(slower["seconds"], estimate["seconds"])

    def test_links_and_symbols_are_not_read_out(self):
        tidy = reading.tidy_for_speaking("Go to https://example.com/a-very-long-link now & then.")
        self.assertNotIn("http", tidy)
        self.assertIn("and", tidy)


# ==========================================================================
# Getting the words out of things
# ==========================================================================
DOCX_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p><w:r><w:t>%s</w:t></w:r></w:p><w:p><w:r><w:t>%s</w:t></w:r></w:p>"
    "</w:body></w:document>"
)


class TestReadingFiles(unittest.TestCase):
    def test_a_plain_text_file(self):
        path = _write("notes.txt", "Hello there.\nThis is a file.")
        text, note = reading.extract(path)
        self.assertIn("Hello there.", text)
        self.assertIn("text", note.lower())

    def test_a_windows_encoded_file(self):
        path = os.path.join(tempfile.mkdtemp(), "old.txt")
        with open(path, "wb") as handle:
            handle.write("Caf\u00e9 na\u00efve r\u00e9sum\u00e9".encode("cp1252"))
        text, _note = reading.extract(path)
        self.assertIn("Café", text)

    def test_a_file_with_a_byte_order_mark(self):
        path = os.path.join(tempfile.mkdtemp(), "bom.txt")
        with open(path, "wb") as handle:
            handle.write("\ufeffHello".encode("utf-8"))
        text, _note = reading.extract(path)
        self.assertNotIn("\ufeff", text)
        self.assertTrue(text.startswith("Hello"))

    def test_a_web_page(self):
        path = _write("page.html",
                      "<html><head><style>p{color:red}</style><script>var x=1;</script></head>"
                      "<body><h1>Title</h1><p>First &amp; foremost.</p>"
                      "<ul><li>One</li><li>Two</li></ul></body></html>")
        text, note = reading.extract(path)
        self.assertIn("First & foremost.", text)
        self.assertIn("• One", text)
        self.assertNotIn("color:red", text)
        self.assertNotIn("var x", text)
        self.assertIn("web page", note)

    def test_a_word_document(self):
        path = os.path.join(tempfile.mkdtemp(), "letter.docx")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", DOCX_TEMPLATE % ("Dear Sir,", "Yours faithfully."))
        text, note = reading.extract(path)
        self.assertIn("Dear Sir,", text)
        self.assertIn("Yours faithfully.", text)
        self.assertIn("Word", note)

    def test_an_e_book(self):
        path = os.path.join(tempfile.mkdtemp(), "book.epub")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("chapter1.xhtml", "<html><body><p>Once upon a time.</p></body></html>")
            archive.writestr("chapter2.xhtml", "<html><body><p>The end.</p></body></html>")
        text, note = reading.extract(path)
        self.assertIn("Once upon a time.", text)
        self.assertIn("The end.", text)
        self.assertIn("e-book", note)

    def test_a_simple_pdf(self):
        content = b"BT /F1 12 Tf 72 720 Td (Hello from a PDF, written by hand.) Tj ET"
        path = self._pdf(content, compress=False)
        text, note = reading.extract(path)
        self.assertIn("Hello from a PDF, written by hand.", text)
        self.assertIn("PDF", note)

    def test_a_compressed_pdf(self):
        import zlib
        content = zlib.compress(b"BT (Compressed words come out too.) Tj ET")
        path = self._pdf(content, compress=True)
        text, _note = reading.extract(path)
        self.assertIn("Compressed words come out too.", text)

    def test_a_pdf_with_escapes_and_hex(self):
        content = br"BT (A parenthesis \(like this\) and a dash - here.) Tj ET"
        path = self._pdf(content, compress=False)
        text, _note = reading.extract(path)
        self.assertIn("(like this)", text)

    @staticmethod
    def _pdf(content: bytes, compress: bool) -> str:
        stream = content
        extra = b"/Filter /FlateDecode " if compress else b""
        body = (b"%PDF-1.4\n"
                b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
                b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
                b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n"
                b"4 0 obj << /Length " + str(len(stream)).encode() + b" " + extra + b">>\nstream\n"
                + stream + b"\nendstream endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n")
        return _write("made-by-hand.pdf", body)

    def test_a_scanned_pdf_says_so_instead_of_reading_nonsense(self):
        """A PDF with no text operations at all is a picture of pages."""
        path = self._pdf(b"q 612 0 0 792 0 0 cm /Im0 Do Q", compress=False)
        with self.assertRaises(reading.Unreadable) as caught:
            reading.extract(path)
        self.assertIn("scan", str(caught.exception))

    def test_an_old_word_file_gets_a_helpful_answer(self):
        path = _write("old.doc", b"\xd0\xcf\x11\xe0old word file")
        with self.assertRaises(reading.Unreadable) as caught:
            reading.extract(path)
        self.assertIn(".docx", str(caught.exception))

    def test_a_file_that_vanished(self):
        with self.assertRaises(reading.Unreadable):
            reading.extract(os.path.join(tempfile.mkdtemp(), "not-here.txt"))

    def test_nonsense_detection(self):
        self.assertTrue(reading.looks_like_nonsense(""))
        self.assertTrue(reading.looks_like_nonsense("~~ ~~ ~~"))
        self.assertFalse(reading.looks_like_nonsense(
            "This is ordinary prose about ordinary things, and it goes on for a while, "
            "in the way that prose about ordinary things tends to do."))


# ==========================================================================
# The voice, quietly
# ==========================================================================
class TestVoices(unittest.TestCase):
    def test_an_engine_is_described_in_the_shape_the_page_expects(self):
        info = voices.describe()
        for key in ("available", "engine", "label", "voices", "note", "can_save", "rate"):
            self.assertIn(key, info)
        self.assertIsInstance(info["voices"], list)
        self.assertIn("min", info["rate"])
        self.assertTrue(info["note"])

    def test_saying_what_it_can_do_is_honest(self):
        current = voices.engine()
        if current is None:
            self.assertFalse(voices.describe()["available"])
            self.assertIn("voice", voices.describe()["note"].lower())
        else:
            self.assertTrue(voices.describe()["available"])
            self.assertTrue(voices.describe()["voices"])

    def test_the_text_is_never_put_inside_the_command(self):
        """The words go in a file; the command only ever names the file."""
        current = voices.engine()
        if current is None or not getattr(current, "queue_mode", False):
            self.skipTest("queue-mode engine not present on this machine")
        nasty = "'; Remove-Item C:\\ -Recurse; $x = '"
        queue = store.scratch_dir() / "test-queue.txt"
        queue.write_text(nasty, encoding="utf-8")
        argv = current.queue_command(queue, "Some Voice", 0, 100)
        joined = " ".join(argv)
        self.assertNotIn("Remove-Item", joined, "the words must not reach the command")
        self.assertIn(queue.name, joined)
        self.assertIsInstance(argv, list)

    def test_an_awkward_voice_name_cannot_break_the_command(self):
        """A voice name with a quote in it must stay a name, not become code."""
        current = voices.engine()
        if current is None or not getattr(current, "queue_mode", False):
            self.skipTest("queue-mode engine not present on this machine")
        queue = store.scratch_dir() / "test-queue.txt"
        queue.write_text("hello", encoding="utf-8")
        argv = current.queue_command(queue, "Voice'; Remove-Item x; '", 0, 100)
        script = argv[-1]
        self.assertIn("Voice''; Remove-Item x; ''", script,
                      "every quote inside the name must be doubled, so the whole thing "
                      "stays a single string the engine reads as a name")
        self.assertEqual(script.count("SelectVoice("), 1)

    def test_joining_pieces_of_a_recording(self):
        folder = tempfile.mkdtemp(prefix="nanosay-wav-")
        parts = []
        for number in (1, 2, 3):
            part = os.path.join(folder, "part-%d.wav" % number)
            with wave.open(part, "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(22050)
                handle.writeframes(b"\x01\x02" * 500)
            parts.append(part)
        target = os.path.join(folder, "joined.wav")
        self.assertTrue(voices.join_wavs(parts, target))
        with wave.open(target, "rb") as handle:
            self.assertEqual(handle.getnframes(), 1500)
        self.assertFalse(voices.join_wavs([os.path.join(folder, "nothing.wav")],
                                          os.path.join(folder, "empty.wav")))

    def test_refreshing_finds_the_same_engine(self):
        first = voices.describe()["engine"]
        voices.refresh()
        self.assertEqual(voices.describe()["engine"], first)


@unittest.skipUnless(voices.engine() is not None and voices.engine().writes_wav,
                     "no voice engine on this machine can write a file")
class TestRecordingForReal(unittest.TestCase):
    """Records a few words to a file. Silent: SAPI writing to a file never
    reaches the speakers, and this is the same code path the button uses."""

    def test_a_short_recording_really_is_a_recording(self):
        target = store.free_name("unit-test.wav")
        ok, message = voices.save_to_wav("This is a test.", target, "", 0)
        self.assertTrue(ok, message)
        self.assertGreater(os.path.getsize(target), 1000)

    def test_recording_in_pieces_gives_one_playable_file(self):
        book = jobs.JobBook()
        sentences = reading.split_sentences("One. Two. Three. Four.")
        job = reader.make_recording("unit test", sentences, "", 0, book)
        for _ in range(600):
            if job.state != "running":
                break
            time.sleep(0.05)
        self.assertEqual(job.state, "done", job.error)
        name = job.result["name"]
        self.assertTrue(name.endswith(".wav"))
        path = store.recordings_dir() / name
        with wave.open(str(path), "rb") as handle:
            self.assertGreater(handle.getnframes(), 1000)
            self.assertGreater(handle.getframerate(), 8000)
        self.assertTrue(store.load_library(), "the recording should be in the library")


# ==========================================================================
# A reading's controls, without speaking
# ==========================================================================
class TestReadingControls(unittest.TestCase):
    def make(self):
        return reader.Reading("test", ["One.", "Two.", "Three."], "", 0, 100)

    def test_pausing_and_carrying_on(self):
        session = self.make()
        self.assertTrue(session.pause())
        self.assertEqual(session.state, "paused")
        self.assertTrue(session.payload()["paused"])
        self.assertTrue(session.resume())
        self.assertEqual(session.state, "reading")

    def test_it_does_not_lose_its_place(self):
        session = self.make()
        session.index = 2
        session.pause()
        session.resume()
        self.assertEqual(session.payload()["index"], 2)
        self.assertEqual(session.payload()["speaking"], "Three.")

    def test_skipping_forward(self):
        session = self.make()
        session.skip()
        self.assertEqual(session.index, 1)
        session.index = session.total
        session.skip()
        self.assertEqual(session.state, "done")

    def test_stopping_says_stopped(self):
        session = self.make()
        self.assertTrue(session.stop())
        self.assertEqual(session.state, "stopped")
        self.assertFalse(session.stop(), "stopping twice is not an error, just nothing to do")

    def test_the_page_can_follow_along(self):
        session = self.make()
        session.index = 1
        payload = session.payload()
        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["index"], 1)
        self.assertEqual(payload["speaking"], "Two.")


# ==========================================================================
# The AI next door
# ==========================================================================
class TestShorten(unittest.TestCase):
    def test_it_says_plainly_when_nanolaama_is_not_running(self):
        state = shorten.look("http://127.0.0.1:9")
        self.assertFalse(state["running"])
        self.assertIn("127.0.0.1:9", state["sentence"])

    def test_no_address_at_all(self):
        self.assertIn("No address", shorten.look("")["sentence"])

    def test_shortening_without_it_says_why(self):
        ok, message = shorten.shorten("Some words to shorten.", "http://127.0.0.1:9")
        self.assertFalse(ok)
        self.assertIn("not running", message)


# ==========================================================================
# The folder it owns
# ==========================================================================
class TestStore(unittest.TestCase):
    def test_settings_round_trip(self):
        store.save_settings({"keep_days": 4, "address": "http://127.0.0.1:9999"})
        self.assertEqual(store.load_settings()["keep_days"], 4)
        self.assertEqual(store.load_settings()["address"], "http://127.0.0.1:9999")
        store.save_settings({"keep_days": "nonsense"})
        self.assertEqual(store.load_settings()["keep_days"], 4)
        store.save_settings({"keep_days": 7, "address": "http://127.0.0.1:8760"})

    def test_a_name_that_is_taken_gets_a_number(self):
        first = store.free_name("reading.wav")
        first.write_bytes(b"x")
        second = store.free_name("reading.wav")
        self.assertNotEqual(first, second)
        first.unlink()

    def test_tidying_leaves_recent_recordings_alone(self):
        old = store.recordings_dir() / "ancient.wav"
        new = store.recordings_dir() / "yesterday.wav"
        old.write_bytes(b"x" * 100)
        new.write_bytes(b"x" * 100)
        ancient = time.time() - 40 * 86400
        os.utime(old, (ancient, ancient))
        outcome = store.tidy(keep_days=7)
        self.assertGreaterEqual(outcome["removed"], 1)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        new.unlink()

    def test_the_library_remembers_and_forgets(self):
        store.forget()
        store.remember({"title": "A letter", "audio": "", "audio_name": "", "sentences": 9})
        self.assertEqual(store.load_library()[0]["title"], "A letter")
        store.forget()
        self.assertEqual(store.load_library(), [])


# ==========================================================================
# The whole app, over HTTP
# ==========================================================================
class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.port = free_port(8796)
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.thread = threading.Thread(
            target=cls.app.serve, kwargs={"host": "127.0.0.1", "port": cls.port,
                                          "open_browser": False, "quiet": True},
            daemon=True)
        cls.thread.start()
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(cls.base + "/api/health", timeout=2) as response:
                    if json.loads(response.read().decode("utf-8")).get("ok"):
                        return
            except Exception:
                time.sleep(0.15)
        raise AssertionError("the app never came up")

    @classmethod
    def tearDownClass(cls):
        cls.app.stop()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return json.loads(exc.read().decode("utf-8"))

    def post(self, path, payload):
        request = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return json.loads(exc.read().decode("utf-8"))

    def deliver(self, name, content):
        body = content if isinstance(content, bytes) else content.encode("utf-8")
        started = self.post("/api/upload/start", {"name": name, "size_mb": len(body) / 1048576})
        self.assertIn("id", started, started)
        offset = 0
        while offset < len(body):
            piece = body[offset:offset + 262144]
            request = urllib.request.Request(
                self.base + "/api/upload/chunk?id=%s&offset=%d" % (started["id"], offset),
                data=piece, method="POST")
            with urllib.request.urlopen(request, timeout=60) as response:
                offset = json.loads(response.read().decode("utf-8"))["received"]
        return self.post("/api/upload/finish", {"id": started["id"]})


class TestApi(ServerCase):
    def test_the_app_says_hello(self):
        self.assertTrue(self.get("/api/health")["ok"])

    def test_the_first_paint_has_everything(self):
        state = self.get("/api/state")
        for key in ("speech", "settings", "library", "readings", "folder", "python"):
            self.assertIn(key, state)

    def test_pasted_words_come_back_as_sentences(self):
        answer = self.post("/api/paste", {"text": "Dr. Patel came at 3 p.m. It was late."})
        self.assertEqual(len(answer["sentences"]), 2)
        self.assertEqual(answer["kind"], "words")
        self.assertEqual(answer["estimate"]["sentences"], 2)
        self.assertGreater(answer["estimate"]["words"], 5)

    def test_pasting_nothing_is_refused(self):
        self.assertIn("error", self.post("/api/paste", {"text": "   "}))

    def test_a_text_file_is_read(self):
        arrived = self.deliver("notes.txt", "First sentence. Second sentence.")
        self.assertEqual(len(arrived["sentences"]), 2)
        self.assertEqual(arrived["title"], "notes")

    def test_a_word_file_is_read(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml",
                             DOCX_TEMPLATE % ("Hello from Word.", "Goodbye from Word."))
        arrived = self.deliver("letter.docx", buffer.getvalue())
        self.assertIn("Hello from Word.", arrived["text"])

    def test_a_file_it_cannot_read_says_why(self):
        answer = self.deliver("picture.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
        self.assertIn("error", answer)

    def test_a_web_address_that_is_not_one_is_refused(self):
        answer = self.post("/api/address", {"url": "not a link"})
        self.assertIn("error", answer)

    def test_a_web_address_that_does_not_work_says_so(self):
        answer = self.post("/api/address", {"url": "http://127.0.0.1:9/nothing"})
        self.assertIn("error", answer)
        self.assertIn("could not fetch", answer["error"])

    def test_the_examples_that_come_with_the_app(self):
        listing = self.get("/api/samples")
        self.assertEqual(len(listing["samples"]), 3)
        letter = self.post("/api/samples/load", {"id": "letter"})
        self.assertGreater(len(letter["sentences"]), 5)
        self.assertIn("error", self.post("/api/samples/load", {"id": "nothing-like-this"}))

    def test_reading_nothing_is_refused(self):
        self.assertIn("error", self.post("/api/read", {"sentences": []}))

    def test_an_unknown_reading_is_a_clean_404(self):
        self.assertIn("error", self.get("/api/reading/deadbeef"))

    def test_an_unknown_action_on_a_reading_is_refused(self):
        # Registered but never started, so this test makes no sound at all.
        session = reader.Reading("a quiet test", ["A sentence."], "", 0, 100)
        server._remember_reading(session)
        answer = self.post("/api/reading/%s/hover" % session.id, {})
        self.assertIn("error", answer)
        self.assertIn("do that with a reading", answer["error"])

    def test_recordings_cannot_be_reached_from_outside(self):
        try:
            with urllib.request.urlopen(self.base + "/api/download/..%2Fsettings.json", timeout=20):
                self.fail("that should not have been served")
        except urllib.error.HTTPError as exc:
            self.assertIn(exc.code, (403, 404))

    def test_the_local_ai_is_reported_honestly(self):
        state = self.get("/api/laama")
        self.assertIn("running", state)
        self.assertTrue(state["sentence"])

    def test_shortening_without_the_local_ai_is_a_clear_message(self):
        answer = self.post("/api/shorten", {"sentences": ["Some words."]})
        self.assertIn("job_id", answer)
        for _ in range(200):
            payload = self.get("/api/job/" + answer["job_id"])
            if payload["state"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(payload["state"], "error")
        self.assertIn("nanoLaama", payload["error"])

    def test_settings_can_be_changed(self):
        answer = self.post("/api/settings", {"address": "http://127.0.0.1:8760", "keep_days": 5})
        self.assertEqual(answer["settings"]["keep_days"], 5)
        self.post("/api/settings", {"keep_days": 7})

    def test_looking_again_reports_the_voices(self):
        answer = self.post("/api/look-again", {})
        self.assertIn("speech", answer)
        self.assertIn("message", answer)

    def test_forgetting_and_clearing(self):
        self.post("/api/samples/load", {"id": "letter"})
        answer = self.post("/api/forget", {"what": "library"})
        self.assertIn("message", answer)
        self.assertEqual(self.get("/api/state")["library"], [])
        self.assertIn("error", self.post("/api/forget", {"what": "the-moon"}))

    def test_the_about_page_points_at_the_other_nano_apps(self):
        about = self.get("/api/about")
        self.assertTrue(about["offline"])
        self.assertIn("nanolaama", json.dumps(about))
        self.assertTrue(about["speech"]["note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
