#!/usr/bin/env python3
"""Create and validate an idempotent, private lab etcd PKI on CT120.

The CA private key stays on CT120, never in Git or on a database VM. Repeated
runs validate and reuse keys; they do not silently rotate live cluster trust.
"""
import argparse
import os
from pathlib import Path
import secrets
import stat
import subprocess
import tempfile

MEMBERS = {
    "db-01": "192.168.150.71",
    "db-02": "192.168.150.72",
    "observer": "192.168.150.21",
}
CLIENT = "patroni-client"


def openssl(*args):
    result = subprocess.run(["openssl", *map(str, args)], text=True,
                            capture_output=True, check=False, timeout=40)
    if result.returncode:
        raise RuntimeError("OpenSSL PKI operation failed; do not rotate existing certificates automatically")
    return result.stdout


def require_private(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Invalid PKI file (not a regular non-symlink): {path.name}")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeError(f"PKI file permissions too broad: {path.name}")


def pair_exists(root, stem):
    key, cert = root / (stem + ".key"), root / (stem + ".pem")
    if key.exists() != cert.exists():
        raise RuntimeError(f"Incomplete cert/key pair for {stem}; inspect, do not regenerate")
    if key.exists():
        require_private(key)
        require_private(cert)
        return True
    return False


def verify(root, stem, ip=None):
    cert = root / (stem + ".pem")
    key = root / (stem + ".key")
    require_private(cert)
    require_private(key)
    openssl("verify", "-CAfile", root / "ca.pem", cert)
    openssl("x509", "-checkend", str(14 * 86400), "-noout", "-in", cert)
    cert_pub = openssl("x509", "-in", cert, "-noout", "-pubkey").strip()
    key_pub = openssl("pkey", "-in", key, "-pubout").strip()
    if cert_pub != key_pub:
        raise RuntimeError(f"Certificate/key mismatch: {stem}")
    if ip:
        sans = openssl("x509", "-in", cert, "-noout", "-ext", "subjectAltName")
        if f"IP Address:{ip}" not in sans:
            raise RuntimeError(f"Missing SAN for {stem}")


def create_pair(root, stem, ip=None):
    if pair_exists(root, stem):
        verify(root, stem, ip)
        return
    with tempfile.TemporaryDirectory(prefix=".pki-", dir=root) as tmp:
        tmp = Path(tmp)
        key = tmp / "key"
        csr = tmp / "request.csr"
        cert = tmp / "cert"
        ext = tmp / "extension.cnf"
        eku = "serverAuth,clientAuth" if ip else "clientAuth"
        parts = [
            "[ extensions ]",
            "basicConstraints = critical,CA:FALSE",
            "keyUsage = critical,digitalSignature,keyEncipherment",
            f"extendedKeyUsage = {eku}",
        ]
        if ip:
            parts.append(f"subjectAltName = IP:{ip},IP:127.0.0.1")
        ext.write_text("\n".join(parts) + "\n")
        openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", key)
        openssl("req", "-new", "-key", key, "-out", csr, "-subj", f"/CN=meowcloud-{stem}")
        openssl("x509", "-req", "-in", csr, "-CA", root / "ca.pem",
                "-CAkey", root / "ca.key", "-set_serial", "0x" + secrets.token_hex(16),
                "-out", cert, "-days", "825", "-sha256",
                "-extfile", ext, "-extensions", "extensions")
        os.chmod(key, 0o600)
        os.chmod(cert, 0o600)
        # Atomically publish each completed file; abort on an interrupted pair.
        os.replace(key, root / (stem + ".key"))
        os.replace(cert, root / (stem + ".pem"))
    verify(root, stem, ip)


def create_ca(root):
    key, cert = root / "ca.key", root / "ca.pem"
    if key.exists() != cert.exists():
        raise RuntimeError("Incomplete CA. Stop and recover PKI from secured backup.")
    if key.exists():
        require_private(key)
        require_private(cert)
        openssl("pkey", "-in", key, "-noout")
        openssl("x509", "-checkend", str(14 * 86400), "-noout", "-in", cert)
        openssl("verify", "-CAfile", cert, cert)
        return
    with tempfile.TemporaryDirectory(prefix=".ca-", dir=root) as tmp:
        tmp = Path(tmp)
        temp_key, temp_cert = tmp / "ca.key", tmp / "ca.pem"
        openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", temp_key)
        openssl("req", "-new", "-x509", "-days", "1825", "-sha256",
                "-key", temp_key, "-out", temp_cert, "-subj", "/CN=MeowCloud DB Etcd CA",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign")
        os.chmod(temp_key, 0o600)
        os.chmod(temp_cert, 0o600)
        os.replace(temp_key, key)
        os.replace(temp_cert, cert)
    openssl("verify", "-CAfile", cert, cert)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    root = Path(args.directory).expanduser()
    os.umask(0o077)
    if args.verify_only and not root.is_dir():
        parser.error("PKI directory does not exist")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or (stat.S_IMODE(root.stat().st_mode) & 0o077):
        parser.error("PKI directory must not be a symlink or accessible to other users")
    if args.verify_only:
        if not (root / "ca.key").exists():
            parser.error("CA is missing")
        create_ca(root)
        for name, ip in MEMBERS.items():
            if not pair_exists(root, name):
                parser.error(f"Missing existing node certificate: {name}")
            verify(root, name, ip)
        if not pair_exists(root, CLIENT):
            parser.error("Missing Patroni client certificate")
        verify(root, CLIENT)
    else:
        create_ca(root)
        for name, ip in MEMBERS.items():
            create_pair(root, name, ip)
        create_pair(root, CLIENT)
    print("PASS: etcd TLS material validated; private CA retained on CT120")


if __name__ == "__main__":
    main()
