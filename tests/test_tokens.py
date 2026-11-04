import json
from pathlib import Path

from gateway import tokens

ROOT = Path(__file__).resolve().parents[1]

# Text whose cost the character rule gets wrong, and the direction it gets it
# wrong in. Dense punctuation and digits are what a clinical payload is full
# of, so this is the case the gateway meets rather than a contrived one.
DENSE = '{"mrn": "AB-9931", "dob": "1954-02-11", "ssn": "123-45-6789"}'

# Long plain words, which is where a real vocabulary merges and charges fewer
# tokens than the character rule. This reference does not merge, so it stays
# dearer here too. The test below pins that, because it is the limitation on
# every shortfall figure measured against this reference.
PLAIN = "administration documentation reconciliation authorisation"


def test_an_empty_payload_costs_nothing():
    assert tokens.estimate("") == 0
    assert tokens.count("") == 0


def test_one_character_costs_one_token():
    """The estimate rounds up. Truncating division would charge nothing here."""
    assert tokens.estimate("a") == 1
    assert tokens.estimate("abcd") == 1
    assert tokens.estimate("abcde") == 2


def test_the_estimate_is_short_on_a_payload_full_of_identifiers():
    """This is the direction that lets a tenant past its budget."""
    assert tokens.estimate(DENSE) < tokens.count(DENSE)
    assert tokens.error(DENSE) < 0


def test_the_reference_is_never_cheaper_than_the_character_rule():
    """So the measured shortfall is one sided and it is an upper bound.

    A real subword vocabulary merges, so it charges fewer tokens than four
    characters each for the long common words that merging is for. This
    reference splits and never merges, so it is at least as dear as the
    character rule on every input and the estimate can only ever come out
    short. Any figure for how much the estimate misses is therefore a bound on
    what a real tokeniser would disagree by and not a prediction of it.

    Pinned rather than fixed. Choosing a constant that let the sign go both
    ways would be fitting the reference to the answer.
    """
    corpus = (ROOT / "corpus" / "data" / "labelled-holdout.jsonl").read_text(encoding="utf-8")
    bodies = [json.loads(line)["text"] for line in corpus.splitlines() if line.strip()]
    bodies += [PLAIN, DENSE, "a", "hello world", "x" * 500]
    assert bodies, "nothing to sweep, so this graded nothing"
    cheaper = [b for b in bodies if tokens.count(b) < tokens.estimate(b)]
    assert not cheaper, (
        "%d of %d bodies cost less under the reference than under the character rule, so "
        "the shortfall is no longer one sided and the limitation needs rewriting"
        % (len(cheaper), len(bodies)))


def test_whitespace_is_free_to_the_counter():
    assert tokens.count("abcd efgh") == tokens.count("abcd      efgh")


def test_the_counter_reproduces():
    assert tokens.count(DENSE) == tokens.count(DENSE)
    assert tokens.count(DENSE) == tokens.count(str(DENSE))


def test_punctuation_costs_one_token_each():
    assert tokens.count("-") == 1
    assert tokens.count("---") == 3


def test_a_longer_payload_never_counts_fewer_tokens():
    growing = ["", "a", "a b", "a b c", "a b c 1234", "a b c 1234 -"]
    counts = [tokens.count(t) for t in growing]
    assert counts == sorted(counts), counts
