"""Contractor accounts: password hashing, signup, settings."""
import hashlib
import hmac
import json
import os

from app.db import get_db, now_iso, row_to_dict

DEFAULT_HOURS = {"mon": ["08:00", "18:00"], "tue": ["08:00", "18:00"], "wed": ["08:00", "18:00"],
                 "thu": ["08:00", "18:00"], "fri": ["08:00", "18:00"], "sat": ["09:00", "14:00"], "sun": None}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split(":")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def create_contractor(email: str, password: str, business_name: str, owner_name: str,
                      trade: str = "roofing contractor", notify_email: str | None = None,
                      phone_number: str | None = None, timezone: str = "America/Toronto",
                      business_hours: dict | None = None, visit_duration_min: int = 60) -> int:
    with get_db() as db:
        return db.execute(
            "INSERT INTO contractors (email, password_hash, business_name, owner_name, trade, phone_number, "
            "notify_email, timezone, business_hours, visit_duration_min, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (email.lower().strip(), hash_password(password), business_name, owner_name, trade,
             phone_number or None, notify_email or email, timezone, json.dumps(business_hours or DEFAULT_HOURS),
             visit_duration_min, now_iso()),
        ).lastrowid


def authenticate(email: str, password: str) -> dict | None:
    with get_db() as db:
        row = row_to_dict(db.execute("SELECT * FROM contractors WHERE email=?", (email.lower().strip(),)).fetchone())
    return row if row and verify_password(password, row["password_hash"]) else None


def update_settings(contractor_id: int, **fields) -> None:
    allowed = {"business_name", "owner_name", "trade", "phone_number", "notify_email", "notify_phone",
               "timezone", "business_hours", "visit_duration_min", "calendar_mailbox"}
    fields = {k: (json.dumps(v) if k == "business_hours" else (v or None)) for k, v in fields.items() if k in allowed}
    if fields:
        with get_db() as db:
            db.execute(f"UPDATE contractors SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                       (*fields.values(), contractor_id))


def contractor_for_number(to_number: str | None) -> dict | None:
    """Which contractor does an inbound call belong to? Match the dialled number; with a single
    contractor (demo), route everything to them."""
    with get_db() as db:
        if to_number:
            row = db.execute("SELECT * FROM contractors WHERE phone_number=?", (to_number,)).fetchone()
            if row:
                return row_to_dict(row)
        rows = db.execute("SELECT * FROM contractors LIMIT 2").fetchall()
    return row_to_dict(rows[0]) if len(rows) == 1 else None
