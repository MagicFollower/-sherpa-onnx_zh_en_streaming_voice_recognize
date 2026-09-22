"""仅生成项目私有CA/服务器证书，不安装系统信任、不修改防火墙。

根CA公钥可人工核对指纹后分发，CA私钥与服务器私钥分目录保存，绝不可进入
静态dist或源码仓库。这是受控自用工具，不提供商业CA或手机兼容性保证。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path

from .artifacts import BACKEND, ROOT


def generate_certificates(output: Path, names: list[str], days: int = 90) -> dict:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    output = output.resolve()
    if not (output.is_relative_to(BACKEND) or output.is_relative_to(ROOT / ".cache" / "backend")):
        raise ValueError("证书只能生成在后端或.cache/backend内")
    if output.exists() or not names or not 1 <= days <= 365:
        raise ValueError("目标已存在、SAN为空或有效期非法；不静默覆盖证书")
    sans = []
    for name in names:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            encoded = name.encode("idna").decode("ascii")
            if len(encoded) > 253 or any(not part or len(part) > 63 or
                    not all(c.isalnum() or c == "-" for c in part) for part in encoded.split(".")):
                raise ValueError("SAN主机名非法") from None
            sans.append(x509.DNSName(encoded))
    now = datetime.now(timezone.utc)
    ca_key, server_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LASR Project Private CA")])
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LASR Local Server")])

    def builder(subject, issuer, public_key, lifetime):
        return (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
                .public_key(public_key).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=lifetime)))

    ca = (builder(ca_name, ca_name, ca_key.public_key(), 366)
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    leaf = (builder(leaf_name, ca.subject, server_key.public_key(), days)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    ca.public_key().verify(leaf.signature, leaf.tbs_certificate_bytes, ec.ECDSA(leaf.signature_hash_algorithm))
    ca.public_key().verify(ca.signature, ca.tbs_certificate_bytes, ec.ECDSA(ca.signature_hash_algorithm))
    staging = output.with_name(output.name + ".partial")
    staging.mkdir(parents=True, exist_ok=False)
    (staging / "ca").mkdir()
    (staging / "server").mkdir()
    pem = serialization.Encoding.PEM

    def write(relative, content, private=False):
        path = staging / relative
        with path.open("xb") as handle:
            handle.write(content)
        if private:
            # Windows ACL继承自当前用户目录；不改OS全局权限/信任。POSIX采用0600。
            os.chmod(path, 0o600)

    key_format = serialization.PrivateFormat.PKCS8
    encryption = serialization.NoEncryption()
    write("ca/ca-key.pem", ca_key.private_bytes(pem, key_format, encryption), True)
    write("ca/ca-cert.pem", ca.public_bytes(pem))
    write("server/server-key.pem", server_key.private_bytes(pem, key_format, encryption), True)
    write("server/server-chain.pem", leaf.public_bytes(pem) + ca.public_bytes(pem))
    metadata = {"san": names, "not_after": leaf.not_valid_after_utc.isoformat(),
                "ca_sha256": ca.fingerprint(hashes.SHA256()).hex(),
                "server_sha256": leaf.fingerprint(hashes.SHA256()).hex(),
                "trust_installed": False, "eku": "serverAuth"}
    write("certificate-metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"))
    staging.rename(output)
    return metadata
