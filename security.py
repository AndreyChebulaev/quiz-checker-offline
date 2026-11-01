"""Хеширование паролей и генерация паролей.

Используется PBKDF2-HMAC-SHA256 из стандартной библиотеки Python
(не требует установки пакетов, работает офлайн). Для каждого пароля
генерируется своя случайная соль. Формат хранения:

    pbkdf2_sha256$<итерации>$<соль hex>$<хеш hex>
"""
import hashlib
import hmac
import secrets
import string

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 200_000
SALT_BYTES = 16

# Без похожих символов (0/O, 1/l/I), чтобы пароль было легко переписать с листка
_PASSWORD_ALPHABET = "".join(
    ch for ch in string.ascii_letters + string.digits if ch not in "0O1lI"
)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"{ALGORITHM}${ITERATIONS}${salt.hex()}${digest.hex()}"


def is_hashed(stored: str) -> bool:
    return isinstance(stored, str) and stored.startswith(ALGORITHM + "$")


def verify_password(password: str, stored: str) -> bool:
    if not password or not is_hashed(stored):
        return False
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    # Сравнение за постоянное время — защита от атак по времени ответа
    return hmac.compare_digest(digest.hex(), digest_hex)


def generate_password(length: int = 10) -> str:
    """Криптостойкий случайный пароль (модуль secrets, а не random)."""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))
