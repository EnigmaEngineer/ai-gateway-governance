# Redacting a response that arrives in pieces

A completion comes back a few tokens at a time. The obvious implementation
redacts each chunk as it lands and it leaks, because an identifier split across
a boundary is not present in either half. Neither half matches and both go out.

The fix is to hold text back until enough has followed it to decide. The plan
for this called that one number. It is three, and the second one is where the
identifiers go.

## The numbers

`holdback` is how much of the arrived text is withheld from the reader. It is
the right edge and it answers an identifier straddling the emit point.

`lookbehind` is how much already emitted text stays in the matching window. It
is the left edge and it answers a labelled class whose label has already gone
out. Most of the Safe Harbor classes enforced here are labelled. `MRN` arrives
in one chunk and `884213` in the next, and a detector handed only the second
chunk sees six digits with nothing saying what they are. No amount of holdback
helps with that, because the holdback governs what has not been sent yet.

`max_window` is the third and the section near the end is where it earns its
place. The first two bound nothing on their own.

## What the sweep measured

The oracle is the whole body redactor over the same text. A span counts as
leaked when any character of it reached the reader unredacted. Half a social
security number beside a tag is still a disclosure.

Bodies come from `corpus/bodies.py`. They are holdout rows joined to completion
length with a tail after the last identifier. The rows are requests and a
request is not a response, so the join is a stand in for response shape.

```
python3 scripts/stream_sweep.py --out measurements/stream-buffers.json
```

```
35 bodies, 281 identifiers found by the whole body redactor
holdback  lookbehind  leaked  diverged
       0         128     252        35
       2         128     202        35
       4         128     157        35
       8         128     100        33
      16         128      22        16
      32         128       0         0
      48         128       0         0
      64         128       0         0
      96         128       0         0
     128         128       0         0
```

Holding nothing back loses 252 of 281. The curve reaches zero at a holdback of
32 characters and stays there.

Then the same sweep with the holdback fixed at a generous 64 and the lookbehind
moved instead.

```
holdback  lookbehind  leaked  diverged
      64           0     124        34
      64           4      60        28
      64           8      51        28
      64          16       0         1
      64          32       0         0
      64          64       0         0
      64         128       0         0
```

**A holdback of 64 with no lookbehind leaks 124 of 281.** That is the finding.
Nearly half the identifiers survive a buffer twice the size the leak curve
needs, because the thing that catches them is not the buffer. The second column
reaches zero at 16 characters, which is about the length of the longest label
in `gateway/phi.py`, and it costs almost nothing to set.

The last column is a weaker question and it does not land in the same place. At
a lookbehind of 16 nothing leaks and one body still comes out different from
the whole body redactor, because an identifier emerged as two tags rather than
one. Nothing was disclosed. A downstream count of redactions would be wrong.

## The holdback is not a bound on identifier length

The plain reading is that a buffer has to be longer than the longest identifier
or the identifier can straddle it. That is wrong here and the reason is the
emit rule rather than the buffer. The redactor never emits through a detected
span, so a pattern that matches a prefix of its own identifier holds the whole
thing however long it runs.

```
long_url    web_urls         91 chars
long_email  email_addresses  71 chars
```

Both of those held at a holdback of 64, which is well under either length.

What the holdback actually has to cover is the longest unmatchable prefix. An
email address has no matchable prefix at all, because the pattern needs the at
sign and the domain, so what has to fit is the local part and that is short.

## The cost

Timings are not committed. A count reproduces on another machine and a
millisecond does not, so the manifest carries buffer sizes and leak counts and
nothing else. The figures below are from one machine on 2026-11-03, a two core
Linux VM on Python 3.10.12. Each arm ran in its own process with the order
rotated so every arm was first once, six passes an arm.

```
python3 scripts/stream_cost.py --arm hb64 --passes 6
```

```
arm        passes      min   median      max
batch         30      6.1      6.3      6.9 ms
hb16          30    227.4    237.7    244.8 ms
hb32          30    248.1    258.1    307.4 ms
hb64          30    296.4    304.7    323.2 ms
hb128         30    366.7    384.5    435.4 ms
```

Eight bodies of about 900 characters each. Streaming at the shipped holdback
costs about 48 times the whole body redactor over the same text, and the ratio
is the part that survives a different machine. The cause is not subtle. The
batch redactor runs its patterns once over the body and the streaming redactor
runs them again on every chunk, which at four characters a chunk is more than
two hundred re-scans of a window that is mostly the same text each time.

The spread does not separate neighbouring arms. The slowest pass of the 32
holdback arm came in above the median of the 64 arm, so the honest reading is
that this run tells the two ends apart and nothing finer. It also says nothing
about a different interpreter or a machine not sharing two cores.

Read against the leak curve the efficient setting is near the knee. The leak
count reaches zero at a holdback of 32 and the cost keeps climbing past it, so
128 buys nothing that 32 did not already buy. The service ships 64 for margin
over the aimed cuts, and on these medians that margin costs about 18 percent.

## The cost of detection over a large payload

Separate question and it was open as a thread. The only bound on payload size
is `MAX_BODY` in the HTTP layer.

```
arm        passes      min   median      max
size8k        24      5.2      5.5      6.1 ms
size64k       24     42.0     43.6     45.8 ms
size256k      24    168.8    173.6    193.1 ms
size1m        24    672.1    694.6    733.3 ms
```

Linear in payload size. One request at the current maximum holds a core for
about seven tenths of a second, which is an availability surface rather than a
correctness one. Nothing here rate limits.

## One long link was a denial of service

The two numbers above bound nothing by themselves and the reason is the emit
rule. The redactor never emits through a detected span, which is the property
that lets a 91 character URL survive a 64 character holdback. The same property
means a span that keeps extending keeps the buffer from draining, and every
chunk re-scans everything held.

```
url chars   body chars    no window
      200          237       4.4 ms
      400          437      13.3 ms
      800          837      46.2 ms
     1600         1637     166.7 ms
     3200         3237     634.4 ms
```

Four times the work for twice the length. One URL is enough to produce it and
the endpoint accepts a megabyte.

`max_window` is the bound. Past it the redactor stops waiting, redacts what it
is holding under the class that was extending, and then drops everything up to
the next whitespace. Both halves are needed. The first version redacted the
held part and nothing else, and the rest of the URL then arrived with no
scheme in front of it, so no pattern matched and the tail went out in the
clear. The bound had turned a slow request into a leaking one.

```
url chars   body chars   window 1024   forced   tail held
      200          237         4.6 ms        0   True
      400          437        14.3 ms        0   True
      800          837        47.7 ms        0   True
     1600         1637        70.5 ms        1   True
     3200         3237        72.0 ms        1   True
    25600        25637        75.9 ms        1   True
   102400       102437        92.9 ms        1   True
```

Flat from 1600 characters to a hundred thousand. The window is not a typed
number either.

```
longest identifier in either slice: 38 chars
```

A test asserts the window is at least eight times that, so adding a class with
longer values fails the suite rather than quietly force redacting ordinary
traffic. Over redacting is the direction to fail in. A reader loses the end of
a long link and nothing that should have been withheld is sent.

## Cuts aimed at the gap

The corpus sweep chunks on a fixed grid. That is the average case and not the
bad one, so eight payloads were cut on purpose at the worst offset available.
The middle of an identifier and the boundary between a label and its value.

```
python3 scripts/split_probe.py --out measurements/split-probe.json
```

```
0 of 8 leaked at holdback 64, 0 diverged from the whole body redactor
```

This is a lower bound and not an estimate. The cuts were chosen with the
implementation open and aimed at the gap, which is the same reason the evasion
pass in `docs/evasion.md` is a lower bound.

## Two versions of this measurement graded nothing

Both are worth recording, because the failure is the same one twice and it is
not visible in the result.

The probe reported eight of eight held on its first run. Every payload was
shorter than the holdback, so the redactor emitted nothing until close and then
ran over the complete body. A check named `graded_as_a_stream` fails the pass
now if any row stops reaching the emit point before close.

The sweep then had the same defect in a quieter form. Bodies were joined to
length without any rule about where the last identifier fell, so a body whose
identifiers all sat inside the final holdback was graded at close. That was 152
body and arm pairs and every one of them reported clean, which biased the leak
counts toward zero. The manifest carries the count now and the suite fails on
anything above it.

A loop over zero items reports no problems and a clean number from an arm that
never ran looks exactly the same. What is worth keeping here is that the arm
did run. It just ran against a body that gave it nothing to do.

## The streamed path redacts and does not assess injection

`/v1/guard/stream` is the response direction. The text is the model talking
rather than the caller, so an injection pattern inside it is not an attack
arriving at the gateway. A summariser asked to explain prompt injection returns
a response full of the words the patterns look for, and blocking that would be
a filter on the subject rather than on the behaviour. Injection is assessed on
the request at `/v1/guard`.

The response carries `x-guardrail-injection-assessed: false` so a reader does
not have to infer it from the path name.

```
x-guardrail-holdback-chars: 64
```

## Known limitations

- The lookbehind holds unredacted text after the reader already has it. The
  exposure is bounded by the lookbehind rather than by the length of the
  response, and it is still a window of raw identifiers in memory.
- Re-scanning the window on every chunk is where the factor of 48 goes.
  Draining less often would cut it, and it would also widen the effective
  holdback and move the leak curve, so it is a third dimension and nothing has
  measured it.
- A client that disconnects part way has read text the redactor had not
  finished deciding about. The response carries the holdback in a header so a
  reader can tell, and nothing enforces what the reader does with it.
- A response that carries an instruction aimed at a downstream agent passes
  through untouched. Assessing the response direction is a different problem
  from assessing the request and nothing here attempts it.
- The oracle is the whole body redactor. Everything the detectors miss outright
  is still missed here, and those figures are in `docs/scoring.md`.

## Re-measured, and the ratio is the part that holds

Every count in `stream-buffers.json` came back identical on the last day of
the build. The cost figures did not, and the ratio between them did.

Thirty passes an arm across five rotations, one arm per process, medians in
milliseconds over eight bodies of about 900 characters:

```
  batch     8.8
  hb16    337.4      38.3 times the batch arm
  hb32    367.6      41.8
  hb64    430.2      48.9
  hb128   540.0      61.4
```

The first run of this read 6.3 ms for the batch arm and 304.7 ms at the
shipped holdback of 64, which is 48.4 times. The two absolute numbers are now
40 percent higher and the ratio is 48.9. So the ratio is a property of the work
and the milliseconds are a property of the afternoon.

The holdback also costs more as it widens, which is the other half of the
drain interval argument. The window is re-scanned on every chunk, so a wider
holdback scans more text more times. Nothing here measures draining less
often, and that is the one dimension of the three that is still unswept.
