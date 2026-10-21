"""
Módulo de autenticación y seguridad para BuscaLeads · FACENIA.

Garantiza acceso restringido exclusivamente a usuarios dados de alta en la
base de datos (sin registro público).
Utiliza hashing criptográfico robusto (PBKDF2-HMAC-SHA256 con sal única)
y tokens de sesión firmados con HMAC-SHA256.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from typing import Any, Dict, Optional, Tuple

from config import DATABASE_PATH, get_supabase_key, get_supabase_url, is_supabase_configured

# Secreto del servidor para la firma de tokens de sesión
_DEFAULT_SECRET = "facenia_buscaleads_auth_secret_key_v1_secure"


def get_auth_secret() -> str:
    return (os.getenv("AUTH_SECRET_KEY") or _DEFAULT_SECRET).strip()


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """Genera un hash seguro PBKDF2-HMAC-SHA256 con sal aleatoria de 16 bytes."""
    clean_pwd = (password or "").strip()
    if not clean_pwd:
        raise ValueError("La contraseña no puede estar vacía.")
    salt_hex = salt or secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        "sha256",
        clean_pwd.encode("utf-8"),
        salt_hex.encode("utf-8"),
        100_000,
    )
    return f"pbkdf2_sha256${salt_hex}${key.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verifica una contraseña contra un hash almacenado."""
    if not password or not stored_hash:
        return False
    try:
        parts = stored_hash.split("$")
        if len(parts) == 3 and parts[0] == "pbkdf2_sha256":
            _, salt_hex, expected_hex = parts
            key = hashlib.pbkdf2_hmac(
                "sha256",
                password.strip().encode("utf-8"),
                salt_hex.encode("utf-8"),
                100_000,
            )
            return hmac.compare_digest(key.hex(), expected_hex)
        elif len(parts) == 2 and parts[0] == "sha256":
            _, expected_hex = parts
            h = hashlib.sha256(password.strip().encode("utf-8")).hexdigest()
            return hmac.compare_digest(h, expected_hex)
    except Exception:
        return False
    return False


def generate_session_token(username: str, expires_in_seconds: int = 86400 * 14) -> str:
    """
    Genera un token firmado con HMAC-SHA256 que incluye el nombre de usuario
    y fecha de caducidad (14 días por defecto).
    Formato: base64url(payload).base64url(signature)
    """
    payload = {
        "u": username.strip().lower(),
        "exp": int(time.time()) + expires_in_seconds,
        "nonce": secrets.token_hex(8),
    }
    payload_raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_raw).decode("ascii").rstrip("=")

    secret = get_auth_secret().encode("utf-8")
    sig = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")

    return f"{payload_b64}.{sig_b64}"


def verify_session_token(token: Optional[str]) -> Optional[str]:
    """
    Verifica la validez y firma de un token de sesión.
    Devuelve el nombre de usuario si es válido y no ha expirado; de lo contrario None.
    """
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.strip().split(".", 1)

        # Verificar firma HMAC
        secret = get_auth_secret().encode("utf-8")
        expected_sig = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
        expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode("ascii").rstrip("=")

        if not hmac.compare_digest(sig_b64, expected_sig_b64):
            return None

        # Decodificar payload
        rem = len(payload_b64) % 4
        padded = payload_b64 + ("=" * (4 - rem) if rem else "")
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))

        if payload.get("exp", 0) < time.time():
            return None

        username = payload.get("u")
        return str(username) if username else None
    except Exception:
        return None


def get_user_from_db(username: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Busca un usuario en la base de datos local SQLite o en Supabase."""
    clean_u = (username or "").strip().lower()
    if not clean_u:
        return None

    # 1. Consulta en SQLite local
    path = db_path or DATABASE_PATH
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, password_hash, role, created_at, last_login FROM users WHERE LOWER(username) = ?",
            (clean_u,),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception:
        pass

    # 2. Si no se encuentra en SQLite y Supabase está configurado, consultar Supabase
    if is_supabase_configured():
        try:
            import requests

            url = f"{get_supabase_url()}/rest/v1/buscaleads_users"
            key = get_supabase_key()
            res = requests.get(
                url,
                params={"username": f"eq.{clean_u}", "select": "id,username,password_hash,role,created_at,last_login"},
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=4,
            )
            if res.ok:
                items = res.json()
                if items:
                    user_data = items[0]
                    # Cachear en SQLite local si la tabla existe
                    _save_user_to_sqlite(user_data, path)
                    return user_data
        except Exception:
            pass

    return None


def _save_user_to_sqlite(user: Dict[str, Any], db_path: Optional[str] = None) -> None:
    try:
        conn = sqlite3.connect(db_path or DATABASE_PATH)
        cur = conn.cursor()
        cur.execute(
            """INSERT OR REPLACE INTO users (username, password_hash, role, created_at, last_login)
               VALUES (?, ?, ?, ?, ?)""",
            (
                user.get("username", "").strip().lower(),
                user.get("password_hash", ""),
                user.get("role", "admin"),
                user.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
                user.get("last_login"),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def update_user_last_login(username: str, db_path: Optional[str] = None) -> None:
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
    clean_u = username.strip().lower()
    try:
        conn = sqlite3.connect(db_path or DATABASE_PATH)
        cur = conn.cursor()
        cur.execute("UPDATE users SET last_login = ? WHERE LOWER(username) = ?", (now_iso, clean_u))
        conn.commit()
        conn.close()
    except Exception:
        pass

    if is_supabase_configured():
        try:
            import requests

            url = f"{get_supabase_url()}/rest/v1/buscaleads_users"
            key = get_supabase_key()
            requests.patch(
                url,
                params={"username": f"eq.{clean_u}"},
                headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"last_login": now_iso},
                timeout=3,
            )
        except Exception:
            pass


def authenticate(username: str, password: str, db_path: Optional[str] = None) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Autentica un usuario contra la base de datos.
    Devuelve (éxito, info_usuario, mensaje_error).
    """
    clean_u = (username or "").strip().lower()
    clean_p = (password or "").strip()
    if not clean_u or not clean_p:
        return False, None, "Por favor, introduce usuario y contraseña."

    user = get_user_from_db(clean_u, db_path)
    if not user:
        return False, None, "Usuario o contraseña incorrectos."

    if not verify_password(clean_p, user.get("password_hash", "")):
        return False, None, "Usuario o contraseña incorrectos."

    update_user_last_login(clean_u, db_path)
    return True, {"username": user["username"], "role": user.get("role", "admin")}, ""
