"""Response shaped bodies built from the labelled rows.

The corpus rows are requests and a request is not a response. Joining rows is
a stand in for response shape and it buys the one property a streaming
measurement needs, which is a body long enough that the emit point passes the
identifiers while text is still arriving.

A body whose last identifier sits inside the final holdback is not a stream. It
emits nothing until close and then the whole body redactor runs over the whole
body, so that arm grades the batch path and reports no leak. The first version
of the sweep had 152 body and arm pairs in that state and the figures it
produced were biased toward clean. So a body is only closed once the text after
its last identifier is longer than the widest buffer any arm will use.
"""

import json
from pathlib import Path

from gateway import phi

TARGET_CHARS = 900


def rows(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    out = [json.loads(l)["text"] for l in lines if l.strip()]
    if not out:
        raise ValueError("%s held no rows, so anything built from it grades nothing" % path)
    return out


def build(path, target=TARGET_CHARS, tail_margin=160):
    """Bodies long enough to stream, plus the rows left over after the last one."""
    pool = rows(path)
    bodies, current = [], []
    for text in pool:
        current.append(text)
        body = " ".join(current)
        if len(body) < target:
            continue
        if _tail_after_last_identifier(body) <= tail_margin:
            continue
        bodies.append(body)
        current = []
    return bodies, len(current)


def _tail_after_last_identifier(body):
    spans = phi.detect(body)
    if not spans:
        return 0
    return len(body) - max(s.end for s in spans)
