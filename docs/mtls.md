# Mutual TLS from the gateway to the backend

The gateway is the only thing meant to reach the model backend. Until this
existed that was a claim about two network objects, a NetworkPolicy and a
firewall rule, and either one being wrong or absent reopened the path with
nothing to show it had happened.

A client certificate is the half of that which does not depend on anybody's
routing table. The backend now refuses a connection that cannot present one
signed by the CA it was given.

```
python3 scripts/make_certs.py --out tls
python3 -m gateway.upstream --port 8443 --tls-ca tls/ca.crt \
    --tls-cert tls/backend.crt --tls-key tls/backend.key
python3 -m gateway.app --port 8080 --upstream https://localhost:8443/ \
    --upstream-ca tls/ca.crt --upstream-cert tls/gateway.crt \
    --upstream-key tls/gateway.key
```

Nothing under `tls/` is committed. It is in `.gitignore` and the probe and the
suite both generate what they need. A private key in a public repository is
still there years later.

## The constructor does not do what its name suggests

`ssl.create_default_context` takes a purpose. `Purpose.CLIENT_AUTH` is the one
you build a server with. Read back on this machine, python 3.10.12 with
OpenSSL 3.0.2:

```
create_default_context(Purpose.CLIENT_AUTH).verify_mode   CERT_NONE
create_default_context(Purpose.SERVER_AUTH).verify_mode   CERT_REQUIRED
```

So a backend built the obvious way serves TLS, presents its own certificate,
and accepts a client that presents nothing at all. The same call for a client
does verify its peer, which is what makes the asymmetry hard to see. Both
lines look like the careful spelling and only one of them is.

From outside, that backend is indistinguishable from one doing mutual TLS.
Every connection is encrypted. The server certificate is real and verified by
the client. The traffic looks right in every tool that reports a cipher. The
only way to find it is to connect without a certificate on purpose and notice
that you got a 200.

`mtls.server_context` therefore sets `verify_mode` by hand rather than
trusting the purpose, and `require_client_cert=False` exists only to build the
control arm.

## A completed handshake is not an accepted client

`measurements/mtls.json` records the handshake and the exchange separately for
each arm, because they disagree.

| Arm | Handshake | Exchange |
| --- | --- | --- |
| hardened backend, the gateway's own certificate | completed | answered |
| hardened backend, no client certificate | completed | refused |
| hardened backend, a valid certificate from an unrelated CA | completed | refused |
| hardened backend, client does not trust the backend's CA | refused | refused |
| hardened backend, a name the backend certificate does not carry | refused | refused |
| hardened backend, the same wrong name with the check turned off | completed | answered |
| default server context, no client certificate | completed | answered |

Two of the seven completed the handshake and were refused afterwards. Both are
cases where the server rejects the client. Under TLS 1.3, which is what every
arm negotiated, the client sends its certificate and its Finished message and
considers the handshake done. The server's verdict arrives as an alert on the
next read.

The two arms that fail at the handshake are the two where the client rejects
the server. A client checks its peer's certificate during the handshake, so
those surface at `wrap_socket`.

That asymmetry is the trap. A probe that calls `wrap_socket` and reports
success has just reported that a client with no certificate was accepted by a
backend that refuses it. A client that treats a successful `wrap_socket` as a
live connection has the same bug, and will report its first read as a network
error rather than as an authentication failure.

## Every refusal has a control beside it

An arm being refused is also what a probe pointed at a dead port looks like.
So no refusal in that table stands on its own.

- The two **no certificate** arms differ only in the server's `verify_mode`.
  One is refused and one is answered, which is what makes the first a property
  of the hardening rather than of the harness.
- The two **wrong name** arms differ only in the client's `check_hostname`.
  One is refused and one is answered.
- The **unrelated CA** arm presents a perfectly valid certificate. It is
  refused because of who signed it, which is the refusal worth having. A
  backend that only turns away clients presenting nothing is checking for
  presence rather than identity.

`scripts/mtls_probe.py` fails rather than writing the manifest when any arm
lands on the wrong side of its expectation. It also fails if nothing got
through at all, if nothing was refused at all, or if the backend did not record
the caller as `ai-gateway`.

## The identity is recorded

The backend reads the common name off the peer certificate and counts it. A
service behind mutual TLS that cannot say who called has paid for the
handshake and thrown away the only thing it bought.

```
GET /counters   {"served": 2, "callers": {"ai-gateway": 2}}
```

The permissive arm shows up in the same map under `no client certificate`,
which is how the probe proves that arm reached the handler at all.

## What this does not do

- **The CA is a test CA with a thirty day life and no revocation.** There is no
  CRL and no OCSP, so a leaked client key is good until the certificate
  expires. A real deployment wants short lived certificates from something that
  issues them. On GKE that means a mesh or a certificate manager, and it is a
  piece of work in its own right rather than a line in this one.
- **The common name is the identity.** Modern practice puts the identity in a
  SAN URI and reads that. The common name is what `openssl req -subj` makes
  easy and it is what this reads.
- **The proxy's own listener is unchanged.** Mutual TLS protects the hop the
  gateway makes. It does nothing about a caller that reaches the third party
  proxy on the pod address directly. That path is still two network objects
  standing in for a property of the process, and it is still open.
- **No handshake happened on a real cluster.** Everything here is loopback on
  one machine. A cluster introduces a load balancer that may terminate TLS, and
  terminating TLS in front of a backend that wants a client certificate is the
  obvious way to delete this control by accident.
