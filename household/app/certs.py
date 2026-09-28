"""Key, CSR and PKCS#12 generation."""

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID


def new_key_and_csr(common_name: str) -> tuple[rsa.RSAPrivateKey, str]:
    # RSA 2048 is the most broadly supported key type across Android KeyChain
    # and iOS SecPKCS12Import.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, common_name[:64]),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Household"),
                ]
            )
        )
        .sign(key, hashes.SHA256())
    )
    return key, csr.public_bytes(serialization.Encoding.PEM).decode()


def build_p12(key: rsa.RSAPrivateKey, cert_pem: str, friendly_name: str, password: str = "") -> bytes:
    """PKCS#12 bundle. Without a password it is unprotected, which both companion apps
    import without prompting (iOS omits the passphrase; Android's installer tries "" first).
    The file is only served once, to the browser that requested it, on the LAN."""
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    if not password:
        encryption = serialization.NoEncryption()
    else:
        # Legacy PBE (3DES + SHA1 MAC): the one format every Android and iOS
        # version imports without complaint.
        encryption = (
            serialization.PrivateFormat.PKCS12.encryption_builder()
            .kdf_rounds(50000)
            .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
            .hmac_hash(hashes.SHA1())
            .build(password.encode())
        )
    return pkcs12.serialize_key_and_certificates(
        friendly_name.encode(), key, cert, None, encryption
    )
