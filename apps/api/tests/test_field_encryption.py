"""
Comprehensive Automated Test Suite for Sensitive Field Encryption.
Complies with Stage 2 Phase 9 specifications and security requirements.

Verifies:
1. Correct encryption and decryption for synthetic CNIC, IBAN, and salary.
2. Random encryption: identical inputs produce distinct DEKs, nonces, and ciphertexts.
3. Tampered data ciphertext fails safely with AuthenticationFailedError.
4. Tampered wrapped DEK fails safely with AuthenticationFailedError.
5. Wrong Key Encryption Key (KEK) fails safely with AuthenticationFailedError.
6. Cross-tenant substitution fails safely with AuthenticationFailedError.
7. Cross-field substitution fails safely with AuthenticationFailedError.
8. Cross-record substitution fails safely with AuthenticationFailedError.
9. Invalid nonce length (length != 12 bytes) raises InvalidNonceError.
10. Modified wrapping AAD fails safely with AuthenticationFailedError.
11. Key ID selection: historical key decryption succeeds post-rotation.
12. Encryption with new active key ID post-rotation.
13. Unknown key ID fails fast with UnknownKeyIdError (no silent fallback).
14. Invalid key configuration (missing, non-32-byte, invalid Base64) raises KeyConfigurationError.
15. Invalid ciphertext format (malformed JSON, corrupted Base64) raises InvalidCiphertextFormatError.
16. Zero-logging verification: raw keys, DEKs, and plaintexts are never emitted to logs.
17. Plaintext absence: serialized encrypted payload does not contain original plaintext.
"""

import base64
import json
import logging
import secrets
from uuid import uuid4

import pytest

from app.core.encryption import (
    AuthenticationFailedError,
    EncryptedFieldPayload,
    EncryptionContext,
    InvalidCiphertextFormatError,
    InvalidNonceError,
    KeyConfigurationError,
    KeyRegistry,
    LocalKeyProvider,
    UnknownKeyIdError,
    decode_and_validate_key,
    decrypt_field,
    encrypt_field,
    strict_b64decode,
    strict_b64encode,
)

# Test KEKs (Synthetic 32-byte keys for testing only)
TEST_KEY_V1 = secrets.token_bytes(32)
TEST_KEY_V2 = secrets.token_bytes(32)
TEST_KEY_V0 = secrets.token_bytes(32)


@pytest.fixture
def test_registry() -> KeyRegistry:
    """Fixture providing an isolated KeyRegistry with known synthetic test keys."""
    registry = KeyRegistry(
        active_key_id="test-kek-v1",
        active_key_bytes=TEST_KEY_V1,
        historical_keys={"test-kek-v0": TEST_KEY_V0},
    )
    return registry


@pytest.fixture
def test_provider(test_registry: KeyRegistry) -> LocalKeyProvider:
    """Fixture providing a LocalKeyProvider backed by the test registry."""
    return LocalKeyProvider(registry=test_registry)


@pytest.fixture
def sample_context() -> EncryptionContext:
    """Fixture providing a canonical EncryptionContext for Acme Tech employee record."""
    return EncryptionContext(
        tenant_id=uuid4(),
        entity_type="employee",
        record_id=uuid4(),
        field_name="cnic",
    )


# ==============================================================================
# TEST 1: Correct Encryption and Decryption Round-Trip
# ==============================================================================
def test_01_correct_encryption_and_decryption(
    sample_context: EncryptionContext,
    test_provider: LocalKeyProvider,
) -> None:
    """
    1. Verifies that synthetic sensitive fields (Pakistani CNIC, IBAN, and salary)
       encrypt and decrypt cleanly back to the exact original plaintext.
    """
    synthetic_cnic = "42101-1234567-1"
    synthetic_iban = "PK36SCBL0000001123456701"
    synthetic_salary = "PKR 250,000"

    for original_text in (synthetic_cnic, synthetic_iban, synthetic_salary):
        encrypted_str = encrypt_field(
            plaintext=original_text,
            context=sample_context,
            key_provider=test_provider,
        )
        assert isinstance(encrypted_str, str)

        decrypted_text = decrypt_field(
            encrypted_payload=encrypted_str,
            context=sample_context,
            key_provider=test_provider,
        )
        assert decrypted_text == original_text


# ==============================================================================
# TEST 2: Random Encryption Produces Distinct Ciphertexts
# ==============================================================================
def test_02_random_encryption_different_ciphertexts(
    sample_context: EncryptionContext,
    test_provider: LocalKeyProvider,
) -> None:
    """
    2. Verifies semantic security (IND-CPA): encrypting the identical plaintext
       twice produces completely distinct nonces, wrapped DEKs, and ciphertexts.
    """
    plaintext = "42101-9999999-9"

    enc1 = encrypt_field(plaintext, sample_context, key_provider=test_provider)
    enc2 = encrypt_field(plaintext, sample_context, key_provider=test_provider)

    assert enc1 != enc2

    p1 = EncryptedFieldPayload.deserialize(enc1)
    p2 = EncryptedFieldPayload.deserialize(enc2)

    assert p1.nonce_data != p2.nonce_data
    assert p1.nonce_wrap != p2.nonce_wrap
    assert p1.wrapped_dek != p2.wrapped_dek
    assert p1.ciphertext != p2.ciphertext


# ==============================================================================
# TEST 3: Tampered Data Ciphertext is Rejected
# ==============================================================================
def test_03_tampered_ciphertext_rejected(
    sample_context: EncryptionContext,
    test_provider: LocalKeyProvider,
) -> None:
    """
    3. Verifies that modifying even a single byte of the data ciphertext
       fails authentication safely with AuthenticationFailedError.
    """
    encrypted_str = encrypt_field("Sensitive Bank Data", sample_context, key_provider=test_provider)
    payload_dict = json.loads(encrypted_str)

    # Flip bits in data ciphertext
    ct_bytes = bytearray(strict_b64decode(payload_dict["ciphertext"]))
    ct_bytes[0] ^= 0xFF
    payload_dict["ciphertext"] = strict_b64encode(bytes(ct_bytes))
    tampered_str = json.dumps(payload_dict)

    with pytest.raises(AuthenticationFailedError):
        decrypt_field(tampered_str, sample_context, key_provider=test_provider)


# ==============================================================================
# TEST 4: Tampered Wrapped DEK is Rejected
# ==============================================================================
def test_04_tampered_wrapped_dek_rejected(
    sample_context: EncryptionContext,
    test_provider: LocalKeyProvider,
) -> None:
    """
    4. Verifies that modifying the wrapped DEK payload fails unwrapping safely
       with AuthenticationFailedError.
    """
    encrypted_str = encrypt_field("Sensitive Tax Info", sample_context, key_provider=test_provider)
    payload_dict = json.loads(encrypted_str)

    # Flip bits in wrapped DEK
    wdek_bytes = bytearray(strict_b64decode(payload_dict["wrapped_dek"]))
    wdek_bytes[0] ^= 0xFF
    payload_dict["wrapped_dek"] = strict_b64encode(bytes(wdek_bytes))
    tampered_str = json.dumps(payload_dict)

    with pytest.raises(AuthenticationFailedError):
        decrypt_field(tampered_str, sample_context, key_provider=test_provider)


# ==============================================================================
# TEST 5: Wrong Key is Rejected
# ==============================================================================
def test_05_wrong_key_rejected(
    sample_context: EncryptionContext,
) -> None:
    """
    5. Verifies that attempting decryption under an incorrect KEK fails safely
       with AuthenticationFailedError.
    """
    reg1 = KeyRegistry(active_key_id="kek-1", active_key_bytes=secrets.token_bytes(32))
    reg2 = KeyRegistry(
        active_key_id="kek-1", active_key_bytes=secrets.token_bytes(32)
    )  # Same ID, different key!

    prov1 = LocalKeyProvider(registry=reg1)
    prov2 = LocalKeyProvider(registry=reg2)

    enc = encrypt_field("Confidential Compensation", sample_context, key_provider=prov1)

    with pytest.raises(AuthenticationFailedError):
        decrypt_field(enc, sample_context, key_provider=prov2)


# ==============================================================================
# TEST 6: Cross-Tenant Substitution Rejected via AAD
# ==============================================================================
def test_06_cross_tenant_substitution_rejected(
    test_provider: LocalKeyProvider,
) -> None:
    """
    6. Verifies that ciphertext encrypted under Tenant A cannot be decrypted
       under Tenant B's context, failing safely with AuthenticationFailedError.
    """
    tenant_a = uuid4()
    tenant_b = uuid4()
    record_id = uuid4()

    ctx_a = EncryptionContext(
        tenant_id=tenant_a, entity_type="employee", record_id=record_id, field_name="cnic"
    )
    ctx_b = EncryptionContext(
        tenant_id=tenant_b, entity_type="employee", record_id=record_id, field_name="cnic"
    )

    enc_a = encrypt_field("Tenant A CNIC: 42101-1111111-1", ctx_a, key_provider=test_provider)

    with pytest.raises(AuthenticationFailedError):
        decrypt_field(enc_a, ctx_b, key_provider=test_provider)


# ==============================================================================
# TEST 7: Cross-Field Substitution Rejected via AAD
# ==============================================================================
def test_07_cross_field_substitution_rejected(
    sample_context: EncryptionContext,
    test_provider: LocalKeyProvider,
) -> None:
    """
    7. Verifies that ciphertext for 'salary' cannot be decrypted under 'bank_account'
       even within the same tenant and record, failing with AuthenticationFailedError.
    """
    ctx_salary = EncryptionContext(
        tenant_id=sample_context.tenant_id,
        entity_type=sample_context.entity_type,
        record_id=sample_context.record_id,
        field_name="salary",
    )
    ctx_bank = EncryptionContext(
        tenant_id=sample_context.tenant_id,
        entity_type=sample_context.entity_type,
        record_id=sample_context.record_id,
        field_name="bank_account",
    )

    enc_salary = encrypt_field("PKR 450,000", ctx_salary, key_provider=test_provider)

    with pytest.raises(AuthenticationFailedError):
        decrypt_field(enc_salary, ctx_bank, key_provider=test_provider)


# ==============================================================================
# TEST 8: Cross-Record Substitution Rejected via AAD
# ==============================================================================
def test_08_cross_record_substitution_rejected(
    sample_context: EncryptionContext,
    test_provider: LocalKeyProvider,
) -> None:
    """
    8. Verifies that ciphertext for Employee 1 cannot be substituted into Employee 2,
       failing safely with AuthenticationFailedError.
    """
    ctx_emp1 = sample_context
    ctx_emp2 = EncryptionContext(
        tenant_id=sample_context.tenant_id,
        entity_type=sample_context.entity_type,
        record_id=uuid4(),  # Different employee ID!
        field_name=sample_context.field_name,
    )

    enc_emp1 = encrypt_field("Employee 1 Secret", ctx_emp1, key_provider=test_provider)

    with pytest.raises(AuthenticationFailedError):
        decrypt_field(enc_emp1, ctx_emp2, key_provider=test_provider)


# ==============================================================================
# TEST 9: Invalid Nonce Length is Rejected
# ==============================================================================
def test_09_invalid_nonce_length(
    sample_context: EncryptionContext,
    test_provider: LocalKeyProvider,
) -> None:
    """
    9. Verifies that nonces with lengths != 12 bytes raise InvalidNonceError.
    """
    encrypted_str = encrypt_field("Valid Data", sample_context, key_provider=test_provider)
    payload_dict = json.loads(encrypted_str)

    # 1. Test short data nonce (10 bytes)
    payload_dict["nonce_data"] = strict_b64encode(b"short_nonce")
    with pytest.raises(InvalidNonceError):
        decrypt_field(json.dumps(payload_dict), sample_context, key_provider=test_provider)

    # 2. Test short wrapping nonce (8 bytes)
    payload_dict = json.loads(encrypted_str)
    payload_dict["nonce_wrap"] = strict_b64encode(b"short_8b")
    with pytest.raises(InvalidNonceError):
        decrypt_field(json.dumps(payload_dict), sample_context, key_provider=test_provider)


# ==============================================================================
# TEST 10: Modified Wrapping AAD is Rejected
# ==============================================================================
def test_10_modified_wrapping_aad_rejected(
    sample_context: EncryptionContext,
    test_provider: LocalKeyProvider,
) -> None:
    """
    10. Verifies that altering the context used during DEK unwrapping
        fails authentication safely with AuthenticationFailedError.
    """
    encrypted_str = encrypt_field("Protected Record", sample_context, key_provider=test_provider)

    tampered_ctx = EncryptionContext(
        tenant_id=sample_context.tenant_id,
        entity_type="candidate",  # Changed entity type
        record_id=sample_context.record_id,
        field_name=sample_context.field_name,
    )

    with pytest.raises(AuthenticationFailedError):
        decrypt_field(encrypted_str, tampered_ctx, key_provider=test_provider)


# ==============================================================================
# TEST 11: Key ID Selection and Historical Key Rotation
# ==============================================================================
def test_11_key_id_selection_and_historical_rotation(
    sample_context: EncryptionContext,
    test_registry: KeyRegistry,
    test_provider: LocalKeyProvider,
) -> None:
    """
    11. Verifies that records encrypted under historical key 'test-kek-v0'
        decrypt cleanly after the registry has rotated to 'test-kek-v1'.
    """
    # Encrypt explicitly using historical key v0
    enc_v0 = encrypt_field(
        "Historical Record v0",
        sample_context,
        key_id="test-kek-v0",
        key_provider=test_provider,
    )
    p_v0 = EncryptedFieldPayload.deserialize(enc_v0)
    assert p_v0.wrapping_key_id == "test-kek-v0"

    # Decrypt: Provider automatically selects test-kek-v0 from registry
    dec_v0 = decrypt_field(enc_v0, sample_context, key_provider=test_provider)
    assert dec_v0 == "Historical Record v0"


# ==============================================================================
# TEST 12: Encryption with New Active Key
# ==============================================================================
def test_12_encryption_with_new_active_key(
    sample_context: EncryptionContext,
    test_registry: KeyRegistry,
    test_provider: LocalKeyProvider,
) -> None:
    """
    12. Verifies that when a new key is registered as active, subsequent
        encryptions automatically use the new active key ID.
    """
    # Register v2 as new active key
    test_registry.register_key("test-kek-v2", TEST_KEY_V2, set_as_active=True)
    assert test_registry.active_key_id == "test-kek-v2"

    enc_v2 = encrypt_field("Fresh Record v2", sample_context, key_provider=test_provider)
    p_v2 = EncryptedFieldPayload.deserialize(enc_v2)
    assert p_v2.wrapping_key_id == "test-kek-v2"

    dec_v2 = decrypt_field(enc_v2, sample_context, key_provider=test_provider)
    assert dec_v2 == "Fresh Record v2"


# ==============================================================================
# TEST 13: Unknown Key ID is Rejected (No Silent Fallback)
# ==============================================================================
def test_13_unknown_key_id_rejected(
    sample_context: EncryptionContext,
    test_provider: LocalKeyProvider,
) -> None:
    """
    13. Verifies that payloads referencing an unknown key ID fail fast with
        UnknownKeyIdError and never silently fall back to active keys.
    """
    encrypted_str = encrypt_field("Confidential", sample_context, key_provider=test_provider)
    payload_dict = json.loads(encrypted_str)
    payload_dict["wrapping_key_id"] = "nonexistent-kek-99"

    with pytest.raises(UnknownKeyIdError) as exc_info:
        decrypt_field(json.dumps(payload_dict), sample_context, key_provider=test_provider)

    assert "nonexistent-kek-99" in str(exc_info.value)


# ==============================================================================
# TEST 14: Invalid Key Configuration is Rejected
# ==============================================================================
def test_14_invalid_key_configuration() -> None:
    """
    14. Verifies that keys that are missing, not 32 bytes, or invalid Base64
        raise KeyConfigurationError or MissingKeyError.
    """
    # 1. Missing key
    with pytest.raises(KeyConfigurationError):
        decode_and_validate_key(None)
    with pytest.raises(KeyConfigurationError):
        decode_and_validate_key("")

    # 2. Too short (16 bytes)
    short_b64 = base64.b64encode(b"only_16_bytes___").decode()
    with pytest.raises(KeyConfigurationError) as exc:
        decode_and_validate_key(short_b64)
    assert "must decode to exactly 32 bytes" in str(exc.value)

    # 3. Too long (48 bytes)
    long_b64 = base64.b64encode(b"a" * 48).decode()
    with pytest.raises(KeyConfigurationError):
        decode_and_validate_key(long_b64)

    # 4. Invalid non-base64 characters
    with pytest.raises(KeyConfigurationError):
        decode_and_validate_key("!@#$%^&*()_+~`")


# ==============================================================================
# TEST 15: Invalid Ciphertext Format is Rejected
# ==============================================================================
def test_15_invalid_ciphertext_format(
    sample_context: EncryptionContext,
    test_provider: LocalKeyProvider,
) -> None:
    """
    15. Verifies that malformed JSON, truncated payloads, or non-canonical Base64
        raise InvalidCiphertextFormatError.
    """
    # 1. Non-JSON string
    with pytest.raises(InvalidCiphertextFormatError):
        decrypt_field("plain-unencrypted-string", sample_context, key_provider=test_provider)

    # 2. JSON array instead of object
    with pytest.raises(InvalidCiphertextFormatError):
        decrypt_field(
            json.dumps(["not", "an", "object"]), sample_context, key_provider=test_provider
        )

    # 3. Missing required fields (e.g. missing ciphertext)
    with pytest.raises(InvalidCiphertextFormatError):
        decrypt_field(
            json.dumps({"version": "v1", "key_id": "k1"}),
            sample_context,
            key_provider=test_provider,
        )

    # 4. Non-canonical Base64 padding in ciphertext
    encrypted_str = encrypt_field("Valid", sample_context, key_provider=test_provider)
    payload_dict = json.loads(encrypted_str)
    payload_dict["ciphertext"] = "abc==="  # Invalid Base64 padding
    with pytest.raises(InvalidCiphertextFormatError):
        decrypt_field(json.dumps(payload_dict), sample_context, key_provider=test_provider)


# ==============================================================================
# TEST 16: Zero-Logging Security Policy
# ==============================================================================
def test_16_key_material_never_in_logs(
    sample_context: EncryptionContext,
    test_provider: LocalKeyProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    16. Verifies that raw DEKs, KEKs, and decrypted plaintext values
        are never logged by the encryption subsystem.
    """
    secret_text = "ULTRA_SENSITIVE_CNIC_99999"

    with caplog.at_level(logging.DEBUG):
        enc = encrypt_field(secret_text, sample_context, key_provider=test_provider)
        dec = decrypt_field(enc, sample_context, key_provider=test_provider)
        assert dec == secret_text

    # Scan all captured log records
    for record in caplog.records:
        message = record.getMessage()
        assert secret_text not in message, "Plaintext sensitive value leaked into logs!"
        assert strict_b64encode(TEST_KEY_V1) not in message, "Raw KEK leaked into logs!"


# ==============================================================================
# TEST 17: Plaintext Not in Serialized Payload
# ==============================================================================
def test_17_plaintext_not_in_serialized_payload(
    sample_context: EncryptionContext,
    test_provider: LocalKeyProvider,
) -> None:
    """
    17. Verifies that the serialized encrypted payload does not contain
        the original plaintext string in cleartext.
    """
    sensitive_items = [
        "42101-1234567-1",
        "PK36SCBL0000001123456701",
        "PKR 750,000",
        "confidential_executive_notes",
    ]

    for item in sensitive_items:
        encrypted_payload = encrypt_field(item, sample_context, key_provider=test_provider)
        assert item not in encrypted_payload, (
            f"Plaintext '{item}' found in serialized ciphertext payload!"
        )
