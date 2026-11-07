"""Generate the certificates the mTLS probe needs, into a directory git ignores.

A private key in a public repository is the kind of mistake that is still
visible years later, so nothing this writes is ever committed and the probe
regenerates whatever it needs. Two certificate authorities come out of this,
because an arm that refuses a client holding no certificate at all proves very
little. The interesting refusal is a client holding a perfectly valid
certificate from the wrong issuer.

    python3 scripts/make_certs.py --out tls

Needs the openssl command line. The cryptography package would do this in
process and it is not a dependency this repository otherwise has, and the
gateway itself is standard library only.
"""

import argparse
import pathlib
import shutil
import subprocess
import sys

# The backend answers on loopback, so the server certificate has to carry both
# spellings a client might connect by. A certificate with no subjectAltName at
# all is rejected outright by anything modern, and the common name has not
# been consulted for this since well before this repository existed.
SERVER_SAN = "DNS:localhost,IP:127.0.0.1"

LEAF_EXT = """basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = %s
subjectAltName = %s
"""


def run(args):
    done = subprocess.run(args, capture_output=True, text=True)
    if done.returncode != 0:
        sys.stderr.write(" ".join(args) + "\n" + done.stderr)
        raise SystemExit("openssl failed with %d" % done.returncode)
    return done


def authority(out, stem, name):
    key = out / ("%s.key" % stem)
    crt = out / ("%s.crt" % stem)
    run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-days", "30", "-sha256",
         "-subj", "/CN=%s" % name,
         "-addext", "basicConstraints=critical,CA:TRUE",
         "-addext", "keyUsage=critical,keyCertSign,cRLSign",
         "-keyout", str(key), "-out", str(crt)])
    return key, crt


def leaf(out, stem, name, ca_key, ca_crt, usage, san):
    key = out / ("%s.key" % stem)
    csr = out / ("%s.csr" % stem)
    crt = out / ("%s.crt" % stem)
    ext = out / ("%s.ext" % stem)
    ext.write_text(LEAF_EXT % (usage, san), encoding="utf-8")
    run(["openssl", "req", "-newkey", "rsa:2048", "-nodes",
         "-subj", "/CN=%s" % name, "-keyout", str(key), "-out", str(csr)])
    run(["openssl", "x509", "-req", "-in", str(csr), "-days", "30", "-sha256",
         "-CA", str(ca_crt), "-CAkey", str(ca_key), "-CAcreateserial",
         "-extfile", str(ext), "-out", str(crt)])
    csr.unlink()
    ext.unlink()
    return key, crt


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="tls", help="directory to write into")
    args = ap.parse_args()
    if not shutil.which("openssl"):
        raise SystemExit("openssl is not on PATH, so there is nothing to generate with")
    out = pathlib.Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    ca_key, ca_crt = authority(out, "ca", "ai-gateway test CA")
    leaf(out, "backend", "localhost", ca_key, ca_crt, "serverAuth", SERVER_SAN)
    leaf(out, "gateway", "ai-gateway", ca_key, ca_crt, "clientAuth", "DNS:ai-gateway")

    rogue_key, rogue_crt = authority(out, "rogue-ca", "unrelated CA")
    leaf(out, "rogue-client", "ai-gateway", rogue_key, rogue_crt,
         "clientAuth", "DNS:ai-gateway")

    written = sorted(p.name for p in out.iterdir())
    print("wrote %d files to %s" % (len(written), out))
    for name in written:
        print("   ", name)


if __name__ == "__main__":
    main()
