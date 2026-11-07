"""TLS contexts for the hop from the gateway to the model backend.

The gateway is the only thing allowed to talk to the backend, and until this
module existed that was a claim about network policy rather than a property of
the connection. Two network objects standing in for an identity is a weak
arrangement, because either one being wrong reopens the path with nothing to
show it. A client certificate is the part that does not depend on anybody's
routing table being right.

What is worth knowing about doing this in Python is in `server_context`. The
constructor you reach for to build a server does not verify the client, and
the same constructor called for a client does verify the server, so the
asymmetry is invisible at the call site.
"""

import ssl

# Measured on this machine, python 3.10.12 with OpenSSL 3.0.2, and the reason
# server_context sets verify_mode by hand rather than trusting the purpose:
#
#   ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).verify_mode  CERT_NONE
#   ssl.create_default_context(ssl.Purpose.SERVER_AUTH).verify_mode  CERT_REQUIRED
#
# CLIENT_AUTH is the purpose for a server. So a backend built the obvious way
# serves TLS, presents its certificate, and accepts a client that presents
# nothing at all. From the outside that is indistinguishable from mutual TLS
# until somebody connects without a certificate on purpose.
DEFAULT_SERVER_VERIFY_MODE_IS_NONE = True


def client_context(ca_file, cert_file, key_file, check_hostname=True):
    """The gateway's side. Verifies the backend and presents our own identity.

    `check_hostname` is a parameter because the probe needs an arm with a
    certificate whose name does not match, and the only way to tell that arm
    from a broken harness is to be able to turn the check off and watch the
    same connection succeed.
    """
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = check_hostname
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    return context


def server_context(ca_file, cert_file, key_file, require_client_cert=True):
    """The backend's side. Requires a client certificate signed by our CA.

    `require_client_cert=False` is the control arm and not a deployment
    option. It is what the default purpose gives you, and the probe uses it to
    show that the refusals in the other arms come from this line.
    """
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    if require_client_cert:
        context.load_verify_locations(cafile=ca_file)
        context.verify_mode = ssl.CERT_REQUIRED
    return context


def describe(context):
    """What a context is actually set to, for a manifest or an assertion.

    Reading these back off the object is the only way to grade the
    configuration without a handshake, and the suite has to run on a clone
    with no certificates in it.
    """
    return {
        "verify_mode": context.verify_mode.name,
        "check_hostname": context.check_hostname,
        "minimum_version": context.minimum_version.name,
    }


def peer_name(sock):
    """The common name the peer presented, or None if it presented nothing.

    The backend wants this in its log so that a request can be attributed to
    an identity rather than to a source address.
    """
    cert = sock.getpeercert()
    if not cert:
        return None
    for rdn in cert.get("subject", ()):
        for key, value in rdn:
            if key == "commonName":
                return value
    return None
