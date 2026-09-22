"""Command line entry point.

Three ways in, in increasing order of nerdiness::

    python start.py              # start the app and open the browser
    python -m nanosay            # the same thing
    python -m nanosay doctor     # list the voices on this computer, and stop
"""

from __future__ import annotations

import argparse
import sys

from . import APP_NAME, __version__, reading, shorten, store, voices


def _doctor() -> int:
    print()
    print("  %s %s — the voices on this computer" % (APP_NAME, __version__))
    print("  " + "-" * 62)
    print("  Python %s" % sys.version.split()[0])
    print("  Your files live in: %s" % store.data_dir())
    print("  Recordings go to:   %s" % store.recordings_dir())
    print()

    engine = voices.engine()
    if engine is None:
        print("  No voice engine found.")
        print()
        print("  " + voices.describe()["note"])
        print()
        print("  Nothing was installed or changed. nanoSay only ever uses a voice that is")
        print("  already here.")
        print()
        return 0

    print("  Engine: %s  (%s)" % (engine.label, engine.id))
    print("  %s" % engine.note())
    print("  Recordings: %s" % ("yes, it can write a file" if getattr(engine, "writes_wav", False)
                                else "no — it can only read aloud"))
    rate = engine.rate_range() if hasattr(engine, "rate_range") else {"min": -10, "max": 10, "default": 0}
    print("  Speed: %s to %s (%s), normal is %s"
          % (rate["min"], rate["max"], rate.get("unit", ""), rate["default"]))
    print()
    print("  Voices")
    for voice in engine.voices():
        print("    %-32s %-8s %s" % (voice["name"], voice["gender"], voice["language"]))
    print()
    print("  One sentence, cut up the way it will be read:")
    sample = ("Dr. Patel arrived at 3 p.m. on Tue. 12 Mar., which cost 3.50 in fares. "
              "“Are you coming?” she asked.")
    for number, sentence in enumerate(reading.split_sentences(sample), start=1):
        print("    %d. %s" % (number, sentence))
    print()
    print("  The AI next door (optional): %s" % shorten.look(store.load_settings().get("address", ""))["sentence"])
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nanosay",
        description="%s — %s" % (APP_NAME, "your computer already has a voice."),
        epilog="Run it with no arguments and a browser window opens.",
    )
    parser.add_argument("command", nargs="?", default="run", choices=["run", "doctor", "version"])
    parser.add_argument("--port", type=int, default=8766, help="which port to use (default 8766)")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this computer only)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    parser.add_argument("--version", action="store_true", help="print the version and stop")
    args = parser.parse_args(argv)

    if args.version or args.command == "version":
        print("%s %s" % (APP_NAME, __version__))
        return 0
    if args.command == "doctor":
        return _doctor()

    store.tidy()

    from .server import create_app

    app = create_app()
    try:
        app.serve(host=args.host, port=args.port, open_browser=not args.no_browser)
    except OSError as exc:
        print("\n  Could not start on %s:%d (%s).\n  Try a different port: --port 8776\n"
              % (args.host, args.port, exc))
        return 1
    finally:
        store.clear_given_files()
    return 0


if __name__ == "__main__":
    sys.exit(main())
