#!/usr/bin/python3
"""A tiny stand-in for clamscan used by the test-suite.

Prints output in clamscan's format. Files containing the EICAR marker are
reported as infected. FAKE_CLAMSCAN_DELAY slows it down to test cancelling.
FAKE_CLAMSCAN_ARGS (a file path) receives the arguments it was called with.
"""
import os
import re
import sys
import time

MARKER = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"


def main(argv):
    if os.environ.get("FAKE_CLAMSCAN_ARGS"):
        with open(os.environ["FAKE_CLAMSCAN_ARGS"], "w") as fh:
            fh.write("\n".join(argv))
    if "--version" in argv:
        print("ClamAV 1.4.3/27800/Sun Oct 25 08:23:01 2026")
        return 0
    excludes, paths = [], []
    for a in argv:
        if a.startswith("--exclude-dir="):
            excludes.append(re.compile(a.split("=", 1)[1]))
        elif not a.startswith("-"):
            paths.append(a)
    if os.environ.get("FAKE_CLAMSCAN_ONLY"):
        paths = [os.environ["FAKE_CLAMSCAN_ONLY"]]
    if os.environ.get("FAKE_CLAMSCAN_NOSCAN"):
        paths = []
    delay = float(os.environ.get("FAKE_CLAMSCAN_DELAY", "0"))
    scanned = infected = errors = 0

    def check(path):
        nonlocal scanned, infected, errors
        if delay:
            time.sleep(delay)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except PermissionError:
            print(f"{path}: Access denied. ERROR", flush=True)
            errors += 1
            return
        scanned += 1
        if not data:
            print(f"{path}: Empty file", flush=True)
        elif MARKER in data:
            infected += 1
            print(f"{path}: Win.Test.EICAR_HC-1 FOUND", flush=True)
        else:
            print(f"{path}: OK", flush=True)

    for top in paths:
        if os.path.isfile(top):
            check(top)
            continue
        for root, dirs, files in os.walk(top):
            if any(r.search(root) for r in excludes):
                print(f"{root}: Excluded", flush=True)
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if not any(r.search(os.path.join(root, d)) for r in excludes)]
            for name in sorted(files):
                check(os.path.join(root, name))

    print()
    print("----------- SCAN SUMMARY -----------")
    print("Known viruses: 8712345")
    print("Engine version: 1.4.3")
    print(f"Scanned files: {scanned}")
    print(f"Infected files: {infected}")
    print(f"Total errors: {errors}")
    print("Data scanned: 0.01 MiB")
    print("Time: 0.123 sec (0 m 0 s)")
    if errors:
        return 2
    return 1 if infected else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
