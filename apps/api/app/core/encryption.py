"""
Sensitive Field Encryption Subsystem for Agentic HR Core.
Complies with Stage 2 Phase 9 specifications and security requirements.

Features:
- AES-256-GCM Envelope Encryption (256-bit ephemeral DEK wrapped by 256-bit KEK).
- Authenticated Associated Data (AAD) binding using canonical sorted JSON (tenant_id, entity_type, record_id, field_name).
- Provider-aware envelope schema supporting local development and future cloud KMS adapters.
- Multi-version Key Registry with dynamic key selection and rotation support.
- Strict Base64 validation (rejects malformed or non-canonical encodings).
- Fail-closed error handling with generic AuthenticationFailedError (protects against tag/oracle leakage).
- Best-effort in-memory key hygiene with documented CPython boundaries.
- Zero-logging security guarantee for raw keys, DEKs, and plaintext values.
"""

import base64
import binascii
import json
import logging
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("agentic_hr.encryption")


# ==============================================================================
# 1. Exception Hierarchy
# ==============================================================================
class FieldEncryptionError(Exception):
    """Base exception for all sensitive field encryption failures."""


class KeyConfigurationError(FieldEncryptionError):
    """Raised when encryption key configuration is missing, invalid, or malformed."""


class MissingKeyError(KeyConfigurationError):
    """Raised when a required Key Encryption Key (KEK) is not configured."""


class UnknownKeyIdError(FieldEncryptionError):
    """Raised when an encrypted payload references a key_id unknown to the registry."""


class InvalidNonceError(FieldEncryptionError):
    """Raised when an initialization vector/nonce length or format is invalid."""


class InvalidCiphertextFormatError(FieldEncryptionError):
    """Raised when an encrypted payload string cannot be parsed or lacks required fields."""


class AuthenticationFailedError(FieldEncryptionError):
    """
    Raised when cryptographic authentication fails.
    Maps AES-GCM InvalidTag caused by tampered ciphertext, wrong key, or AAD context mismatch.
    Never reveals the specific failure root-cause to avoid oracle leakage.
    """


# ==============================================================================
# 2. Context & Envelope Payload Schemas
# ==============================================================================
@dataclass(frozen=True)
class EncryptionContext:
    """
    Cryptographic context bound to both data encryption and DEK wrapping via AAD.
    All attributes must originate strictly from trusted server-side state.
    """

    tenant_id: UUID | str
    entity_type: str
    record_id: UUID | str
    field_name: str

    def to_aad(self) -> bytes:
        """
        Serializes context into deterministic, canonical UTF-8 bytes for AES-GCM AAD.
        Uses sorted JSON with compact separators and canonicalized lowercase UUID strings
        to guarantee identical byte representations across UUID objects and uppercase strings.
        """

        def _canonicalize_id(val: UUID | str) -> str:
            s = str(val).strip()
            try:
                return str(UUID(s))
            except ValueError:
                return s

        canonical_dict = {
            "entity_type": str(self.entity_type).strip(),
            "field_name": str(self.field_name).strip(),
            "record_id": _canonicalize_id(self.record_id),
            "tenant_id": _canonicalize_id(self.tenant_id),
        }
        return json.dumps(canonical_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")


class EncryptedFieldPayload(BaseModel):
    """
    Self-describing, provider-aware envelope encryption payload.
    Compatible with both local AES-256-GCM wrapping and future Cloud KMS providers.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = Field(default="v1", description="Envelope format version")
    algorithm: str = Field(default="AES-256-GCM", description="Cipher used for data encryption")
    wrapping_provider: str = Field(
        default="local",
        description="Key provider used to wrap DEK (e.g. 'local', 'aws_kms', 'gcp_kms')",
    )
    wrapping_algorithm: str = Field(
        default="AES-256-GCM", description="Cipher or mechanism used to wrap DEK"
    )
    wrapping_key_id: str = Field(..., description="Identifier of the Key Encryption Key (KEK) used")
    wrap_metadata: dict[str, Any] = Field(
        default_factory=dict, description="Provider-specific attributes"
    )
    nonce_wrap: str | None = Field(
        default=None,
        description="Base64 12-byte wrapping nonce (required for local AES-GCM, null for KMS)",
    )
    wrapped_dek: str = Field(..., description="Base64-encoded encrypted Data Encryption Key")
    nonce_data: str = Field(..., description="Base64-encoded 12-byte data encryption nonce")
    ciphertext: str = Field(
        ..., description="Base64-encoded field ciphertext with appended 16-byte GCM tag"
    )

    def serialize(self) -> str:
        """Serializes payload into canonical JSON string for database column storage."""
        return self.model_dump_json()

    @classmethod
    def deserialize(cls, data: str) -> "EncryptedFieldPayload":
        """
        Parses JSON string into EncryptedFieldPayload.
        Enforces strict Base64 validation on all encoded fields.
        """
        if not data or not isinstance(data, str):
            raise InvalidCiphertextFormatError("Encrypted payload must be a non-empty string.")

        try:
            parsed = json.loads(data)
            if not isinstance(parsed, dict):
                raise TypeError("Payload must be a JSON object.")
            payload = cls.model_validate(parsed)
        except Exception as exc:
            raise InvalidCiphertextFormatError(
                f"Malformed encrypted payload format: {exc}"
            ) from exc

        # Strict Base64 validation of required cryptographic components
        try:
            strict_b64decode(payload.nonce_data)
            strict_b64decode(payload.wrapped_dek)
            strict_b64decode(payload.ciphertext)
            if payload.nonce_wrap is not None:
                strict_b64decode(payload.nonce_wrap)
        except (binascii.Error, ValueError) as b64_err:
            raise InvalidCiphertextFormatError(
                f"Non-canonical or malformed Base64 encoding in envelope: {b64_err}"
            ) from b64_err

        return payload


# ==============================================================================
# 3. Helper Functions & Strict Encoding
# ==============================================================================
def strict_b64decode(val: str) -> bytes:
    """
    Decodes a Base64 string with strict validation.
    Rejects malformed characters, invalid lengths, or non-canonical padding.
    """
    if not isinstance(val, str) or not val.strip():
        raise binascii.Error("Empty or non-string Base64 input.")
    return base64.b64decode(val.strip(), validate=True)


def strict_b64encode(data: bytes) -> str:
    """Encodes bytes to canonical Base64 string."""
    return base64.b64encode(data).decode("utf-8")


def decode_and_validate_key(raw_key: str | None, key_name: str = "FIELD_ENCRYPTION_KEY") -> bytes:
    """
    Decodes and validates that an encryption key is exactly 32 bytes (256 bits).
    Supports Base64 (44 chars) and Hex (64 chars) representations.
    """
    if not raw_key or not str(raw_key).strip():
        raise MissingKeyError(f"Encryption key '{key_name}' is not configured.")

    clean_str = str(raw_key).strip()

    # Try Hex decoding first if exactly 64 hex characters
    if len(clean_str) == 64:
        try:
            decoded = bytes.fromhex(clean_str)
            if len(decoded) == 32:
                return decoded
        except ValueError:
            pass

    # Try strict Base64 decoding
    try:
        decoded = base64.b64decode(clean_str, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise KeyConfigurationError(
            f"Encryption key '{key_name}' is not valid Base64 or Hex encoding: {exc}"
        ) from exc

    if len(decoded) != 32:
        raise KeyConfigurationError(
            f"Encryption key '{key_name}' must decode to exactly 32 bytes (256 bits); got {len(decoded)} bytes."
        )

    return decoded


# ==============================================================================
# 4. Key Provider Abstract Architecture & Local Provider
# ==============================================================================
class KeyProvider(ABC):
    """
    Abstract interface for envelope encryption key management.
    Separates application-tier field encryption from the key wrapping mechanism.
    Supports current local AES-256-GCM wrapping and future cloud KMS adapters.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider identifier string (e.g. 'local', 'aws_kms', 'gcp_kms')."""

    @abstractmethod
    def wrap_dek(
        self,
        dek: bytes,
        aad: bytes,
        key_id: str | None = None,
    ) -> tuple[str, str, str | None, dict[str, Any]]:
        """
        Wraps ephemeral DEK using the specified or active KEK.
        Returns: (wrapped_dek_b64, wrapping_key_id, nonce_wrap_b64, wrap_metadata)
        """

    @abstractmethod
    def unwrap_dek(
        self,
        wrapped_dek_b64: str,
        wrapping_key_id: str,
        nonce_wrap_b64: str | None,
        wrap_metadata: dict[str, Any],
        aad: bytes,
    ) -> bytes:
        """
        Unwraps wrapped DEK back to raw 32-byte DEK.
        Raises AuthenticationFailedError, UnknownKeyIdError, InvalidNonceError.
        """


class KeyRegistry:
    """
    Registry managing local active and historical Key Encryption Keys (KEKs).
    Enforces startup validation of key lengths and zero silent fallback.
    """

    def __init__(
        self,
        active_key_id: str,
        active_key_bytes: bytes,
        historical_keys: dict[str, bytes] | None = None,
    ) -> None:
        if len(active_key_bytes) != 32:
            raise KeyConfigurationError("Active key must be exactly 32 bytes.")
        self.active_key_id = active_key_id
        self._keys: dict[str, bytes] = {active_key_id: active_key_bytes}

        if historical_keys:
            for k_id, k_bytes in historical_keys.items():
                if len(k_bytes) != 32:
                    raise KeyConfigurationError(
                        f"Historical key '{k_id}' must be exactly 32 bytes."
                    )
                self._keys[k_id] = k_bytes

    def register_key(self, key_id: str, key_bytes: bytes, set_as_active: bool = False) -> None:
        """Registers an additional key into the local registry (useful for testing rotation)."""
        if len(key_bytes) != 32:
            raise KeyConfigurationError(f"Key '{key_id}' must be exactly 32 bytes.")
        self._keys[key_id] = key_bytes
        if set_as_active:
            self.active_key_id = key_id

    def get_key(self, key_id: str) -> bytes:
        """
        Retrieves key bytes for a given key_id.
        Raises UnknownKeyIdError if not found. Never falls back silently.
        """
        if key_id not in self._keys:
            raise UnknownKeyIdError(
                f"Wrapping key ID '{key_id}' is unknown or not configured in KeyRegistry."
            )
        return self._keys[key_id]

    @classmethod
    def from_settings(cls) -> "KeyRegistry":
        """Constructs KeyRegistry from application configuration."""
        from app.core.config import settings

        active_id = settings.FIELD_ENCRYPTION_KEY_ID or "dev-kek-v1"
        active_bytes = decode_and_validate_key(
            settings.FIELD_ENCRYPTION_KEY, "FIELD_ENCRYPTION_KEY"
        )

        historical: dict[str, bytes] = {}
        if settings.FIELD_ENCRYPTION_SECONDARY_KEYS:
            try:
                parsed_secondary = json.loads(settings.FIELD_ENCRYPTION_SECONDARY_KEYS)
                if isinstance(parsed_secondary, dict):
                    for h_id, h_raw in parsed_secondary.items():
                        historical[h_id] = decode_and_validate_key(h_raw, f"SECONDARY_KEY_{h_id}")
            except Exception as exc:
                raise KeyConfigurationError(
                    f"Malformed FIELD_ENCRYPTION_SECONDARY_KEYS JSON: {exc}"
                ) from exc

        return cls(
            active_key_id=active_id, active_key_bytes=active_bytes, historical_keys=historical
        )


class LocalKeyProvider(KeyProvider):
    """
    Local AES-256-GCM envelope key provider.
    Wraps ephemeral DEKs using AES-256-GCM with a dedicated 96-bit wrapping nonce.
    """

    def __init__(self, registry: KeyRegistry | None = None) -> None:
        self.registry = registry or KeyRegistry.from_settings()

    @property
    def provider_name(self) -> str:
        return "local"

    def wrap_dek(
        self,
        dek: bytes,
        aad: bytes,
        key_id: str | None = None,
    ) -> tuple[str, str, str | None, dict[str, Any]]:
        target_key_id = key_id or self.registry.active_key_id
        kek_bytes = self.registry.get_key(target_key_id)

        # Generate 12-byte (96-bit) wrapping nonce
        nonce_wrap = secrets.token_bytes(12)

        # Wrap DEK using AES-256-GCM bound to canonical AAD
        cipher = AESGCM(kek_bytes)
        wrapped_dek_bytes = cipher.encrypt(nonce_wrap, dek, aad)

        return (
            strict_b64encode(wrapped_dek_bytes),
            target_key_id,
            strict_b64encode(nonce_wrap),
            {},
        )

    def unwrap_dek(
        self,
        wrapped_dek_b64: str,
        wrapping_key_id: str,
        nonce_wrap_b64: str | None,
        wrap_metadata: dict[str, Any],
        aad: bytes,
    ) -> bytes:
        if not nonce_wrap_b64:
            raise InvalidNonceError("Local AES-GCM key provider strictly requires nonce_wrap.")

        try:
            nonce_bytes = strict_b64decode(nonce_wrap_b64)
        except (binascii.Error, ValueError) as exc:
            raise InvalidNonceError(f"Malformed Base64 wrapping nonce: {exc}") from exc

        if len(nonce_bytes) != 12:
            raise InvalidNonceError(
                f"Wrapping nonce must be exactly 12 bytes; got {len(nonce_bytes)}."
            )

        try:
            wrapped_dek_bytes = strict_b64decode(wrapped_dek_b64)
        except (binascii.Error, ValueError) as exc:
            raise InvalidCiphertextFormatError(f"Malformed Base64 wrapped DEK: {exc}") from exc

        kek_bytes = self.registry.get_key(wrapping_key_id)
        cipher = AESGCM(kek_bytes)

        try:
            dek = cipher.decrypt(nonce_bytes, wrapped_dek_bytes, aad)
        except InvalidTag as tag_err:
            raise AuthenticationFailedError(
                "Cryptographic authentication failed: wrapped DEK, key, or context mismatch."
            ) from tag_err

        if len(dek) != 32:
            raise AuthenticationFailedError("Unwrapped DEK length invalid.")

        return dek


# ==============================================================================
# 5. Primary Field Encryption & Decryption Functions
# ==============================================================================
def encrypt_field(
    plaintext: str,
    context: EncryptionContext,
    key_id: str | None = None,
    key_provider: KeyProvider | None = None,
) -> str:
    """
    Encrypts a sensitive plaintext field using AES-256-GCM Envelope Encryption.

    Steps:
    1. Generates an ephemeral 256-bit (32-byte) Data Encryption Key (DEK).
    2. Encrypts plaintext with DEK using AES-256-GCM and a fresh 12-byte data nonce.
    3. Wraps DEK with KEK using AES-256-GCM and a fresh 12-byte wrapping nonce.
    4. Binds both data encryption and DEK wrapping to canonical sorted JSON AAD.
    5. Practises best-effort memory clearing on mutable DEK buffer.
    6. Returns self-describing serialized EncryptedFieldPayload JSON string.
    """
    if plaintext is None or not isinstance(plaintext, str):
        raise ValueError("Plaintext field must be a valid string.")

    provider = key_provider or LocalKeyProvider()
    canonical_aad = context.to_aad()

    # 1. Ephemeral DEK & Data Nonce generation
    # Best-effort mutable buffer for in-memory hygiene
    dek_buf = bytearray(secrets.token_bytes(32))
    nonce_data = secrets.token_bytes(12)

    try:
        # 2. Encrypt data with DEK
        dek_cipher = AESGCM(bytes(dek_buf))
        ciphertext_bytes = dek_cipher.encrypt(nonce_data, plaintext.encode("utf-8"), canonical_aad)

        # 3. Wrap DEK with KEK
        wrapped_dek_b64, wrapping_key_id, nonce_wrap_b64, wrap_meta = provider.wrap_dek(
            bytes(dek_buf), canonical_aad, key_id=key_id
        )

        # 4. Construct envelope payload
        payload = EncryptedFieldPayload(
            version="v1",
            algorithm="AES-256-GCM",
            wrapping_provider=provider.provider_name,
            wrapping_algorithm="AES-256-GCM",
            wrapping_key_id=wrapping_key_id,
            wrap_metadata=wrap_meta,
            nonce_wrap=nonce_wrap_b64,
            wrapped_dek=wrapped_dek_b64,
            nonce_data=strict_b64encode(nonce_data),
            ciphertext=strict_b64encode(ciphertext_bytes),
        )
        return payload.serialize()

    finally:
        # Best-effort short-lived memory clearing
        # Note: In CPython, physical zeroization of immutable intermediate copies
        # cannot be guaranteed due to runtime memory allocations.
        dek_buf[:] = b"\x00" * len(dek_buf)
        del dek_buf


def decrypt_field(
    encrypted_payload: str | EncryptedFieldPayload,
    context: EncryptionContext,
    key_provider: KeyProvider | None = None,
) -> str:
    """
    Decrypts an envelope-encrypted field using AES-256-GCM.

    Steps:
    1. Deserializes and validates EncryptedFieldPayload.
    2. Constructs canonical sorted JSON AAD from trusted server-side context.
    3. Unwraps ephemeral DEK using the KEK identified by wrapping_key_id.
    4. Decrypts field ciphertext using unwrapped DEK and nonce_data.
    5. Maps any GCM InvalidTag to generic AuthenticationFailedError.
    6. Practises best-effort memory clearing on unwrapped DEK buffer.
    7. Returns original plaintext string.
    """
    if isinstance(encrypted_payload, str):
        payload = EncryptedFieldPayload.deserialize(encrypted_payload)
    elif isinstance(encrypted_payload, EncryptedFieldPayload):
        payload = encrypted_payload
    else:
        raise InvalidCiphertextFormatError(
            "Payload must be a JSON string or EncryptedFieldPayload."
        )

    provider = key_provider or LocalKeyProvider()
    canonical_aad = context.to_aad()

    # 0. Validate envelope metadata against downgrade / algorithm substitution
    if payload.version != "v1":
        raise InvalidCiphertextFormatError(
            f"Unsupported encrypted payload version '{payload.version}'. Expected 'v1'."
        )
    if payload.algorithm != "AES-256-GCM":
        raise InvalidCiphertextFormatError(
            f"Unsupported data encryption algorithm '{payload.algorithm}'. Expected 'AES-256-GCM'."
        )
    if payload.wrapping_algorithm != "AES-256-GCM":
        raise InvalidCiphertextFormatError(
            f"Unsupported key wrapping algorithm '{payload.wrapping_algorithm}'. Expected 'AES-256-GCM'."
        )
    if payload.wrapping_provider != provider.provider_name:
        raise InvalidCiphertextFormatError(
            f"Envelope wrapping_provider '{payload.wrapping_provider}' does not match active provider '{provider.provider_name}'."
        )

    # 1. Validate data nonce
    try:
        nonce_data_bytes = strict_b64decode(payload.nonce_data)
    except (binascii.Error, ValueError) as exc:
        raise InvalidNonceError(f"Malformed Base64 data nonce: {exc}") from exc

    if len(nonce_data_bytes) != 12:
        raise InvalidNonceError(
            f"Data nonce must be exactly 12 bytes; got {len(nonce_data_bytes)}."
        )

    # 2. Validate data ciphertext bytes
    try:
        ciphertext_bytes = strict_b64decode(payload.ciphertext)
    except (binascii.Error, ValueError) as exc:
        raise InvalidCiphertextFormatError(f"Malformed Base64 ciphertext: {exc}") from exc

    # 3. Unwrap DEK
    raw_dek = provider.unwrap_dek(
        wrapped_dek_b64=payload.wrapped_dek,
        wrapping_key_id=payload.wrapping_key_id,
        nonce_wrap_b64=payload.nonce_wrap,
        wrap_metadata=payload.wrap_metadata,
        aad=canonical_aad,
    )

    dek_buf = bytearray(raw_dek)
    try:
        # 4. Decrypt data ciphertext
        dek_cipher = AESGCM(bytes(dek_buf))
        plaintext_bytes = dek_cipher.decrypt(nonce_data_bytes, ciphertext_bytes, canonical_aad)
        return plaintext_bytes.decode("utf-8")

    except InvalidTag as tag_err:
        raise AuthenticationFailedError(
            "Cryptographic authentication failed: ciphertext, key, or context mismatch."
        ) from tag_err

    finally:
        # Best-effort short-lived memory clearing
        dek_buf[:] = b"\x00" * len(dek_buf)
        del dek_buf
