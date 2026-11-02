# The classes this service does not handle

The enumeration in `gateway/policy.py` is the Safe Harbor list at
45 CFR 164.514(b)(2). It is fixed by regulation. It is not the list of things
the detectors happen to cover, and keeping those two lists apart is the point.

A redaction service built as a pile of patterns refuses what it names and
passes everything else. The refusal is visible in the code and the pass is
invisible, so a class nobody wrote a pattern for is not protected by default.
It is exposed by default, and nothing in the repository would say so unless the
complement is written down.

So every one of the eighteen classes sits in exactly one of two sets.
`ENFORCED` has fifteen members and each is a claim that something tries, not a
claim that it succeeds. The measured recall per class is what says how well.
`DECLARED_OMISSIONS` has three and each carries the reason and what would have
to change.

`tests/test_policy.py` fails on a class in neither set, on a class in both, and
on a name that is not in the regulation at all. The last one matters because a
typo in a category name looks like a covered class and leaves the real one
unclassified.

## The three

**Biometric identifiers.** A fingerprint or voice template reaches a chat API as
an opaque base64 blob inside an attachment field. This service reads text
payloads and does not decode attachments, so it cannot tell a voice print from
any other blob. Closing this needs attachment handling and a content type allow
list rather than another pattern.

**Full face photographs.** The same shape and the same reason. An image is a
blob to this service. A deployment that must cover it belongs behind an
upstream filter that can open the image.

**Any other unique identifying number or characteristic or code.** The
regulation's catch-all is unbounded by construction, so no pattern list
satisfies it. It is named here because it is the reason this service redacts the
enumerated classes and does not claim de-identification. A caller who needs
that claim needs a human review step as well.

## Why the third one is the important entry

The first two are a scope boundary. The third is a statement about what a
pattern based redactor can be. Leaving it off the list would let the README
claim something the code cannot do, and the claim would pass every check in the
repository because nothing here measures a class nobody named.
