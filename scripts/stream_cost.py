"""Times one arm in one process and prints the passes. Writes no manifest.

A measurement in this repository commits a manifest of counts and a wall time
is never one of them, so this writes nothing. It prints, the caller captures
the print, and the figure reaches a reader as prose naming the machine with a
spread rather than as a committed number.

One arm per process, because an interpreter that has already run the detectors
over a megabyte is not the interpreter a cold request arrives at. The caller
rotates the order so each arm is first once. Pass to pass spread on a shared
machine is wide enough to swamp a small difference and ordering is the part
that is cheap to control.
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from corpus import bodies as corpus_bodies  # noqa: E402
from gateway import phi  # noqa: E402
from gateway.stream import redact_stream  # noqa: E402

TOKEN_CHARS = 4
TIMED_BODIES = 8
CORPUS = ROOT / "corpus/data/labelled-holdout.jsonl"

SIZE_ARMS = {"size8k": 8192, "size64k": 65536, "size256k": 262144, "size1m": 1 << 20}
STREAM_ARMS = {"batch": None, "hb16": 16, "hb32": 32, "hb64": 64, "hb128": 128}


def bodies():
    built, _ = corpus_bodies.build(CORPUS)
    return built[:TIMED_BODIES]


def padded(n):
    base = " ".join(corpus_bodies.rows(CORPUS))
    return (base * ((n // len(base)) + 1))[:n]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", required=True)
    ap.add_argument("--passes", type=int, default=6)
    args = ap.parse_args()

    if args.arm in SIZE_ARMS:
        payload = padded(SIZE_ARMS[args.arm])
        work = lambda: phi.detect(payload)
    elif args.arm in STREAM_ARMS:
        corpus = bodies()
        if not corpus:
            raise SystemExit("no bodies, so this arm timed nothing")
        hb = STREAM_ARMS[args.arm]
        if hb is None:
            work = lambda: [phi.redact(b) for b in corpus]
        else:
            work = lambda: [redact_stream([b[i:i + TOKEN_CHARS] for i in range(0, len(b), TOKEN_CHARS)],
                                          holdback=hb, lookbehind=128) for b in corpus]
    else:
        raise SystemExit("unknown arm %r" % args.arm)

    # One throwaway before timing anything. The first call through a regex
    # charges compilation to whichever pass happens to be first.
    work()
    for i in range(args.passes):
        t0 = time.perf_counter()
        work()
        print("arm %-8s pass %d %.1f ms" % (args.arm, i, (time.perf_counter() - t0) * 1000))


if __name__ == "__main__":
    main()
