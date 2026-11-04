from gateway import tokens, upstream

LONG_PROMPT = "the quick brown fox jumped over the lazy dog " * 40


def test_the_ceiling_is_honoured():
    """The reservation covers the ceiling, so an overrun here would break it."""
    answer = upstream.complete(LONG_PROMPT, 10)
    assert answer["usage"]["completion_tokens"] <= 10


def test_the_ceiling_binds_where_the_ratio_would_exceed_it():
    """A prompt short enough to stay under the ceiling tests nothing."""
    wanted = int(tokens.count(LONG_PROMPT) * upstream.ANSWER_RATIO)
    assert wanted > 10, "the fixture no longer reaches the ceiling, so it grades nothing"


def test_the_answer_is_shorter_than_the_ceiling_where_the_ratio_is_the_bound():
    answer = upstream.complete("hello", 4096)
    assert answer["usage"]["completion_tokens"] < 4096


def test_the_usage_adds_up():
    answer = upstream.complete(LONG_PROMPT, 64)
    usage = answer["usage"]
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_the_reported_completion_count_is_the_text_it_returned():
    """A usage block the caller cannot re-derive is a number to be trusted."""
    answer = upstream.complete(LONG_PROMPT, 64)
    assert answer["usage"]["completion_tokens"] == tokens.count(answer["text"])
    assert answer["usage"]["prompt_tokens"] == tokens.count(LONG_PROMPT)


def test_the_same_prompt_costs_the_same_twice():
    first = upstream.complete(LONG_PROMPT, 64)
    second = upstream.complete(LONG_PROMPT, 64)
    assert first == second
