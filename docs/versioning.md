# Versioning, and the two headers that announce a retirement

The gateway serves `v1` and `v2`. `v2` is current. `v1` answers, and every
response it sends says when it was deprecated and when it stops answering.

## Why there are two

`v1` accepts a chat request that declares no `max_output_tokens` and charges it
against a default of 256. That default is in `gateway/app.py` and it arrived
with the token quota, because refusing those requests outright would have made
the quota something a caller opted into.

It is still the wrong behaviour. A caller that declares nothing gets a budget
decision it never made, and when the reservation turns out to be too small the
caller sees a refusal it has no way to predict. This is the same shape as a
relation missing from an allow list being readable. The absence is the grant.

`v2` refuses the request and names the field. That is a breaking change for
every caller built against `v1`, which is the reason a deprecation window
exists rather than a changelog entry.

```
POST /v2/chat  {"text": "..."}                     400, name the field
POST /v2/chat  {"text": "...", "max_output_tokens": 32}   200
POST /v1/chat  {"text": "..."}                     200, charged against 256
```

## The registry is the allow list

`gateway/versioning.py` holds one entry per version this gateway serves.
`split()` returns the version only when the path's first segment is a key in
that table, so `/v3/chat` and `/chat` are both refused with the list of
versions that do exist.

The alternative is a prefix rule that strips anything matching `/v<digits>/`
and routes the rest. That reads as equivalent and it is not. A client written
against `v1` would start receiving `v2` behaviour the day a typo put `v12` in
its base URL, and a client with no version at all would be pinned to whatever
happened to be current when somebody wrote it. The refusal is the feature.

`/healthz` sits outside every version. A health check that moves with the API
version is one nobody can configure once.

## The two date formats

`Deprecation` and `Sunset` almost always travel together and they do not share
a format. This is the one thing about the pair worth knowing.

| Field | Format | Example |
| --- | --- | --- |
| `Deprecation` | Structured Fields Date, RFC 9745 section 2.1 | `@1790812800` |
| `Sunset` | HTTP-date, RFC 8594 section 3 | `Thu, 01 Apr 2027 00:00:00 GMT` |

`Deprecation` is an Item Structured Header Field whose value must be a Date as
per RFC 9651 section 3.3.7. An sf-date serialises as `@` followed by whole
seconds since the epoch. Without the sigil it parses as an integer and means
nothing.

`Sunset` is an HTTP-date, which RFC 9110 section 5.6.7 requires a sender to
write as IMF-fixdate. That production ends in `GMT` and the ABNF spells it
`%s"GMT"`, which is case sensitive. `UTC` and `+0000` and `Z` are all refused.

### Three one-liners and only one of them is right

Measured on this machine, python 3.10.12, with the system clock on UTC.

```
strftime("%a, %d %b %Y %H:%M:%S %Z", time.gmtime(e))     Thu, 01 Apr 2027 00:00:00 GMT
strftime("%a, %d %b %Y %H:%M:%S %Z", time.localtime(e))  Thu, 01 Apr 2027 00:00:00 UTC
email.utils.formatdate(e)                                Thu, 01 Apr 2027 00:00:00 -0000
email.utils.formatdate(e, usegmt=True)                   Thu, 01 Apr 2027 00:00:00 GMT
```

Two of the four are not HTTP-dates. On a machine set to UTC every other
character is identical, so the whole difference between a conformant field and
a broken one is the last three or five characters. On a machine in any other
zone the `localtime` spelling is also the wrong instant, which is loud. The UTC
machine is the dangerous one, and a CI runner is a UTC machine.

`http_date()` uses `email.utils.formatdate(..., usegmt=True)` and a test
matches its output against the IMF-fixdate grammar written out by hand rather
than against the function that produced it.

### The authoritative example is wrong about this

RFC 9745 section 4 is the one place in either document that shows the two
fields together. It prints:

```
Deprecation: @1688169599
Sunset: Sun, 30 Jun 2024 23:59:59 UTC
```

That `Sunset` is not an HTTP-date. The same RFC flags the format difference in
the sentence above the example, saying that for historical reasons the Sunset
header field uses a different data format for date, and then writes the zone
as `UTC`. RFC 8594's own two examples both use `GMT`.

So copying the worked example produces a field the grammar refuses. The value
is kept in `versioning.RFC9745_SECTION_4_EXAMPLE_SUNSET` with a check asserting
it fails the grammar, because the reason this module does not reach for
`strftime` is that the authority is wrong here, and a later reader deleting a
long comment should trip over a failing test instead.

## The link relations come from two different RFCs

```
Link: <https://github.com/.../docs/versioning.md>; rel="deprecation"; type="text/html"
Link: </v2/chat>; rel="successor-version"
```

RFC 9745 registers exactly one relation, `deprecation`, described as referring
to documentation intended for human consumption about the deprecation of the
link's context. It names no successor relation anywhere. Earlier drafts of that
work recommended `successor-version` and `latest-version` and publication
dropped both.

`successor-version` is real and it is RFC 5829 section 3.6. Emitting it is
fine. Citing RFC 9745 for it is not, and that distinction is a row in the
sourced table rather than a comment, because citing the wrong document is the
same class of error as inventing the name.

## The one ordering rule

RFC 9745 section 4 carries the only MUST NOT in either document. The Sunset
timestamp must not be earlier than the Deprecation timestamp. A `Version`
whose sunset precedes its deprecation raises at construction, and equal is
allowed, because equal is not earlier.

Nothing else here is enforceable by a registry on its own. Both documents say
these fields are hints, and RFC 9745 section 5 is explicit that deprecation
does not change the behaviour of the resource.

## What this does not do

- **The dates are policy and nothing measured them.** `v1` is marked deprecated
  on 2026-10-01 with a sunset on 2027-04-01. Six months is a choice.
- **Nothing stops serving `v1` on the sunset date.** The field announces an
  intention. A gateway that enforced it would need a clock in the router and a
  decision about what a sunset response looks like, and refusing a working
  caller on a date in a config file is a bigger decision than this.
- **No caller is counted.** The honest next step is per version request counts,
  so that the sunset date can be argued from traffic rather than from a
  calendar. The counters endpoint does not break down by version.
- **Sourced from the RFCs and not from a conformance suite.** The grammar in
  `tests/test_versioning.py` is transcribed from RFC 9110 section 5.6.7 by
  hand. It accepts the examples in both RFCs that are meant to be valid and
  refuses the one that is not, which is the whole extent of the evidence.
