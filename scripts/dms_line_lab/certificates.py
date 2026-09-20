"""Trust only within the dedicated lab containers; never installs host trust."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

root = Path("/lab-certs")
root.mkdir(exist_ok=True)
if not (root / "line.pem").exists():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Pearnly LOCAL LINE simulator")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("api.line.me"), x509.DNSName("api-data.line.me")]), critical=False)
            .sign(key, hashes.SHA256()))
    (root / "line.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (root / "line.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                     serialization.NoEncryption()))
    (root / "line.key").chmod(0o600)
bundle = Path(ssl.get_default_verify_paths().cafile).read_bytes() + b"\n" + (root / "line.pem").read_bytes()
(root / "ca-bundle.pem").write_bytes(bundle)
print("Local test CA prepared; host and production trust stores unchanged")
