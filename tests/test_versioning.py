"""The registry, and the two date formats the pair of headers does not share.

These run on a clone with nothing installed. The headers are strings built
from a table, so there is nothing here that needs a socket.
"""

import re

import pytest

from gateway import versioning

# IMF-fixdate, written out rather than imported, because the point is to grade
# the output against the grammar and not against the function that made it.
# Two digit day. Three letter month. Four digit year. The literal GMT.
IMF_FIXDATE = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), "
    r"\d{2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4} "
    r"\d{2}:\d{2}:\d{2} GMT$")

SF_DATE = re.compile(r"^@-?\d+$")


def test_every_sourced_row_carries_a_quote_and_a_url():
    """A rule resting on a fact nobody read is the defect this table exists for."""
    assert versioning.SOURCED, "the table is empty, so this check graded nothing"
    for key, row in versioning.SOURCED.items():
        assert row["quote"].strip(), "%s has no quote" % key
        assert row["url"].startswith("https://www.rfc-editor.org/rfc/"), (
            "%s does not cite an RFC" % key)
        assert row["note"].strip(), "%s has no note saying why it matters" % key


def test_an_unsourced_fact_raises_rather_than_returning_none():
    with pytest.raises(KeyError):
        versioning.sourced("deprecation-is-whatever-seems-reasonable")


def test_deprecation_is_an_sf_date_with_the_sigil():
    """An integer without the @ parses as an integer and means nothing."""
    assert versioning.sf_date(1688169599) == "@1688169599"
    assert SF_DATE.match(versioning.sf_date(0))


def test_sunset_is_an_imf_fixdate_ending_in_the_literal_gmt():
    stamp = versioning.http_date(versioning.epoch("2027-04-01T00:00:00Z"))
    assert stamp == "Thu, 01 Apr 2027 00:00:00 GMT"
    assert IMF_FIXDATE.match(stamp), stamp


def test_the_zone_token_is_never_the_zone_name():
    """UTC and +0000 and Z are all refused by the grammar and all look fine."""
    for seconds in (0, 1688169599, versioning.epoch("2026-10-01T00:00:00Z")):
        stamp = versioning.http_date(seconds)
        assert stamp.endswith(" GMT"), stamp
        for wrong in (" UTC", " +0000", " -0000", "Z"):
            assert not stamp.endswith(wrong), stamp


def test_the_example_rfc_9745_prints_beside_deprecation_is_not_an_http_date():
    """The one worked example of the pair uses UTC, and IMF-fixdate wants GMT.

    Kept as a check rather than a comment because the reason this module does
    not reach for strftime is that the authoritative example is wrong, and a
    later reader deleting the long docstring should trip over this.
    """
    assert not IMF_FIXDATE.match(versioning.RFC9745_SECTION_4_EXAMPLE_SUNSET)
    assert versioning.RFC9745_SECTION_4_EXAMPLE_SUNSET.endswith(" UTC")


def test_a_sunset_before_its_deprecation_is_refused():
    """The only MUST NOT in RFC 9745 and the only one a registry can break alone."""
    with pytest.raises(ValueError, match="sunsets before"):
        versioning.Version("vx", "deprecated", deprecation="2027-01-01T00:00:00Z",
                           sunset="2026-01-01T00:00:00Z")


def test_the_same_instant_for_both_is_allowed():
    """MUST NOT be earlier. Equal is not earlier."""
    v = versioning.Version("vx", "deprecated", deprecation="2027-01-01T00:00:00Z",
                           sunset="2027-01-01T00:00:00Z")
    assert v.sunset == v.deprecation


def test_a_deprecated_version_with_no_date_is_refused():
    with pytest.raises(ValueError, match="no deprecation date"):
        versioning.Version("vx", "deprecated")


def test_a_current_version_carrying_a_deprecation_date_is_refused():
    with pytest.raises(ValueError, match="claims to be"):
        versioning.Version("vx", "current", deprecation="2027-01-01T00:00:00Z")


def test_a_current_version_sends_no_deprecation_headers():
    """A header saying a resource is fine is noise on every response forever."""
    assert versioning.VERSIONS[versioning.CURRENT].headers() == []


def test_the_deprecated_version_sends_the_whole_set():
    headers = dict(versioning.VERSIONS["v1"].headers())
    assert headers["deprecation"] == "@%d" % versioning.epoch("2026-10-01T00:00:00Z")
    assert headers["sunset"] == "Thu, 01 Apr 2027 00:00:00 GMT"


def test_the_deprecation_link_uses_the_only_relation_rfc_9745_registers():
    links = [v for k, v in versioning.VERSIONS["v1"].headers() if k == "link"]
    assert any('rel="deprecation"' in link for link in links)
    assert any("rel=\"successor-version\"" in link for link in links)
    # RFC 9745 registers one relation. The successor one is RFC 5829 and the
    # table has to say so, because citing the wrong document is the same
    # class of error as inventing the name.
    assert versioning.sourced("successor-version-comes-from-rfc5829")["url"].endswith(
        "rfc5829.html#section-3.6")


def test_the_epoch_helper_reads_the_stamp_as_utc():
    """mktime would read it as local time, which is silent on a UTC machine."""
    assert versioning.epoch("1970-01-01T00:00:00Z") == 0
    assert versioning.epoch("2023-06-30T23:59:59Z") == 1688169599


def test_an_unknown_version_does_not_resolve_to_the_current_one():
    for path in ("/v9/chat", "/v0/chat", "/V1/chat", "/chat", "/", "/vv1/chat"):
        name, _ = versioning.split(path)
        assert name is None, "%s resolved to %s" % (path, name)


def test_a_known_version_keeps_the_rest_of_the_path():
    assert versioning.split("/v1/guard/stream") == ("v1", "/guard/stream")
    assert versioning.split("/v2/chat") == ("v2", "/chat")


def test_the_registry_is_the_allow_list_and_not_a_prefix_rule():
    """Every routed version is a key. There is no pattern to widen by accident."""
    assert set(versioning.VERSIONS) == {"v1", "v2"}
    assert versioning.CURRENT in versioning.VERSIONS
    assert "v3" not in versioning.VERSIONS
