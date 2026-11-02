"""Prompt injection detection, organised by attack class rather than by regex.

The classes come from the published taxonomy of what these attacks try to do.
An attack overrides the standing instruction. It reassigns the role. It pulls
the system prompt out. It forges a turn boundary. It exfiltrates the context.
It encodes the payload to get past the filter. Or it leans on the model to stop
refusing. Grouping the patterns that way is not decoration. The score is
reported per class, so a class carrying one weak pattern stays visible instead
of being averaged into a headline.

What this cannot do is tell an attack from a discussion of one. A security
engineer asking how to defend against instruction override writes most of the
same words as the attack. That is the precision problem and it is measured.
"""

import re

CLASSES = (
    "instruction_override",
    "role_reassignment",
    "system_prompt_extraction",
    "delimiter_forgery",
    "context_exfiltration",
    "encoding_evasion",
    "refusal_suppression",
)

_PATTERNS = {
    "instruction_override": [
        r"\b(?:ignore|disregard|forget|override|discard)\b[^.!?\n]{0,40}\b(?:previous|prior|above|earlier|preceding|all)\b[^.!?\n]{0,30}\b(?:instruction|prompt|rule|direction|guideline|constraint)",
        r"\b(?:instead\s+of|rather\s+than)\b[^.!?\n]{0,30}\bwhat\s+you\s+(?:were|have\s+been)\s+told\b",
        r"\bnew\s+(?:instruction|rule|directive)s?\s*(?::|follow)",
    ],
    "role_reassignment": [
        r"\byou\s+are\s+(?:now|from\s+now\s+on)\b",
        r"\b(?:act|behave|respond)\s+as\s+(?:if\s+you\s+(?:are|were)|an?\s+)\b[^.!?\n]{0,40}\b(?:unrestricted|uncensored|unfiltered|no\s+(?:rules|limits|restrictions))",
        r"\b(?:pretend|imagine)\s+(?:you\s+(?:are|have)|that\s+you)\b[^.!?\n]{0,40}\b(?:no\s+(?:rules|guidelines|restrictions)|are\s+not\s+bound)",
        r"\b(?:developer|debug|god|dan|jailbreak)\s+mode\b",
        r"\benter\s+(?:unrestricted|maintenance|sudo)\s+mode\b",
    ],
    "system_prompt_extraction": [
        r"\b(?:repeat|print|output|reveal|show|display|echo|recite)\b[^.!?\n]{0,30}\b(?:system\s+prompt|initial\s+(?:prompt|instruction)|your\s+instruction)",
        r"\bwhat\s+(?:were|was)\s+(?:you|your)\b[^.!?\n]{0,20}\b(?:told|instructed|system\s+prompt)\b",
        r"\b(?:verbatim|word\s+for\s+word)\b[^.!?\n]{0,30}\b(?:instruction|prompt)s?\b",
        r"\brepeat\s+everything\s+(?:above|before)\b",
    ],
    "delimiter_forgery": [
        r"<\|?\s*(?:im_start|im_end|system|endoftext|assistant)\s*\|?>",
        r"(?:^|\n)\s*(?:#{2,}|\[|-{3,})\s*(?:system|assistant|developer)\s*(?:\]|:|#{2,}|-{3,})",
        r"(?:^|\n)\s*(?:system|assistant)\s*:\s*(?:you|ignore|now)\b",
        r"\[\s*/?\s*(?:INST|SYS)\s*\]",
    ],
    "context_exfiltration": [
        r"\b(?:send|post|forward|upload|transmit|exfiltrate)\b[^.!?\n]{0,40}\b(?:conversation|transcript|context|history|credential|api\s+key|token)\b",
        r"\binclude\b[^.!?\n]{0,30}\b(?:api\s+key|secret|token|credential|password)\b[^.!?\n]{0,30}\b(?:in|within)\b",
        r"\b(?:curl|fetch|wget)\b[^\n]{0,40}\?\s*(?:data|q|payload)\s*=",
        r"!\[[^\]]*\]\(\s*https?://[^)]*\{[^)]*\}\s*\)",
    ],
    "encoding_evasion": [
        r"\b(?:decode|base64\s*decode|from\s*base64|rot13|atob)\b[^.!?\n]{0,40}\b(?:then|and)\b[^.!?\n]{0,30}\b(?:follow|execute|run|obey|do)\b",
        r"\b(?:base64|rot13)\b[^.!?\n]{0,30}\b(?:instruction|payload|command)s?\b",
        r"\b[A-Za-z0-9+/]{48,}={0,2}\b[^\n]{0,60}\b(?:decode|execute|follow)\b",
        r"\b[1l!][gq9]n[0o]r[e3]\b[^.!?\n]{0,20}\b(?:pr[e3]v[i1][o0]us|[a4]b[o0]v[e3])\b",
    ],
    "refusal_suppression": [
        r"\b(?:do\s+not|don't|never)\b[^.!?\n]{0,20}\b(?:refuse|decline|say\s+you\s+(?:cannot|can't)|apolog)",
        r"\bwithout\s+(?:any\s+)?(?:warning|disclaimer|caveat|refusal)s?\b",
        r"\byou\s+must\s+(?:comply|answer|respond)\b[^.!?\n]{0,30}\bregardless\b",
        r"\bno\s+matter\s+(?:what|your)\b[^.!?\n]{0,25}\b(?:rule|guideline|policy|instruction)s?\b",
    ],
}

_COMPILED = {
    cls: [re.compile(p, re.IGNORECASE) for p in pats]
    for cls, pats in _PATTERNS.items()
}

# Phrases that put the surrounding sentence in quotation rather than in use.
# A payload saying an attacker might type ignore all previous instructions is
# somebody describing the attack. This is the one precision lever in the file
# and it is deliberately narrow, because a wide one is an evasion instruction.
_QUOTING = re.compile(
    r"\b(?:example\s+of|such\s+as|for\s+instance|attacker(?:s)?\s+(?:might|may|could|would|often)|"
    r"defend\s+against|detect(?:ing)?|test\s+(?:case|payload|string)|known\s+attack|"
    r"red\s*team|csv\s+row|sample\s+payload)\b",
    re.IGNORECASE,
)


def classify(text):
    """Classes that fired on this payload, with the matched substring each.

    Returns a list of two item tuples. A payload can fire on several classes
    and that is normal, because a real attack usually stacks two or three.
    """
    if text is None:
        raise ValueError("classify was handed None, which is a caller bug")
    hits = []
    for cls in CLASSES:
        for rx in _COMPILED[cls]:
            m = rx.search(text)
            if m:
                hits.append((cls, m.group(0)))
                break
    return hits


def looks_quoted(text):
    """Whether the payload reads as a description of an attack.

    Used to downgrade rather than to clear. A payload that fires and also looks
    quoted is reported as flagged with the hedge attached, because a filter that
    an attacker can switch off by writing the words for instance is worthless.
    """
    return bool(_QUOTING.search(text or ""))


def assess(text):
    """The service level verdict for one payload."""
    hits = classify(text)
    return {
        "flagged": bool(hits),
        "classes": sorted({c for c, _ in hits}),
        "evidence": [m for _, m in hits],
        "reads_as_quotation": looks_quoted(text) if hits else False,
    }
