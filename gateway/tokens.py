"""Two ways to count tokens, and the gap between them is the point.

A gateway has to decide whether to admit a request before it has the answer.
The provider counts tokens with its own tokeniser and reports the total after
the fact, so the gateway is working from a guess at admission time and a fact
at reconciliation time. Everything in gateway/quota.py exists because those
two numbers differ.

`estimate` is the guess. Four characters to a token is the rule of thumb every
provider's documentation offers and it is what a gateway without the vendor's
tokeniser is left with.

`count` stands in for the provider's answer. It is a word and punctuation
split, which is closer to how a subword tokeniser behaves than the character
rule is. Digits and punctuation each cost their own token and long words cost
fewer than their length suggests. It is NOT a real BPE vocabulary and nothing
here claims the gap it produces is the gap a real provider would produce. What
it gives is a reconciliation that can disagree with the estimate for the same
reasons a real one does.
"""

import re

CHARS_PER_TOKEN = 4

# A subword tokeniser splits on orthographic boundaries before it merges
# anything, so this is the first pass of one and not an approximation of the
# merges. Runs of letters stay whole, runs of digits are cut into pairs because
# that is what the common vocabularies do to numbers, and everything else is
# one token per character.
_WORD = re.compile(r"[A-Za-z]+|\d+|\s+|[^A-Za-z\d\s]")

# A long word is more than one token. Four letters is the knee in most
# published vocabularies and the exact figure does not matter here, because
# this side is the reference and not the thing being graded.
LETTERS_PER_SUBWORD = 4


def estimate(text):
    """What the gateway charges against the budget before forwarding.

    Rounds up, so an empty string costs nothing and one character costs one.
    """
    if not text:
        return 0
    return -(-len(text) // CHARS_PER_TOKEN)


def count(text):
    """What the upstream reports once it has read the text."""
    total = 0
    for piece in _WORD.findall(text):
        if piece.isspace():
            # Whitespace is absorbed into the token that follows it in every
            # vocabulary worth the name, so it is free here.
            continue
        if piece.isalpha():
            total += -(-len(piece) // LETTERS_PER_SUBWORD)
        elif piece.isdigit():
            total += -(-len(piece) // 2)
        else:
            total += 1
    return total


def error(text):
    """Signed difference between the guess and the count.

    Negative means the estimate was short, which is the direction that lets a
    tenant past its budget.
    """
    return estimate(text) - count(text)
