#!/usr/bin/env python3
"""Admin tool: create (or update) a clinician login. No public sign-up exists,
so this is how clinician accounts come into being.

Usage (from push-service/):
    python -m scripts.seed_clinician --username jlee --name "Dr. J. Lee" \
        --role physician --password "temp-Passw0rd"

If --password is omitted, a random temporary password is generated and printed
once. The clinician is flagged must_change_password=True so they set their own
on first login. Roles: physician | nurse | navigator.

Targets the same database the service uses: set DATABASE_URL for Neon/Postgres,
otherwise it writes to the local SQLite file (KURA_DATA_DIR or ./data).
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone

# Allow running as `python scripts/seed_clinician.py` or `python -m scripts...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import auth as auth_lib  # noqa: E402
from app.db import Clinician, build_engine, make_session_factory  # noqa: E402

ROLES = {"physician", "nurse", "navigator"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or update a clinician login.")
    parser.add_argument("--username", required=True, help="login username (stored lowercased)")
    parser.add_argument("--name", required=True, help="display name, e.g. 'Dr. J. Lee'")
    parser.add_argument("--role", default="navigator", choices=sorted(ROLES))
    parser.add_argument("--password", default=None, help="temp password (random if omitted)")
    parser.add_argument(
        "--no-force-change", action="store_true",
        help="do NOT require a password change on first login",
    )
    args = parser.parse_args()

    username = args.username.strip().lower()
    password = args.password or secrets.token_urlsafe(9)

    engine = build_engine(os.getenv("DATABASE_URL", ""))
    Session = make_session_factory(engine)
    db = Session()
    try:
        existing = (
            db.query(Clinician).filter(Clinician.username == username).one_or_none()
        )
        if existing is None:
            row = Clinician(
                id=str(uuid.uuid4()),
                username=username,
                display_name=args.name,
                role=args.role,
                password_hash=auth_lib.hash_password(password),
                must_change_password=not args.no_force_change,
                is_active=True,
                created_at=datetime.now(timezone.utc),
            )
            db.add(row)
            action = "Created"
        else:
            existing.display_name = args.name
            existing.role = args.role
            existing.password_hash = auth_lib.hash_password(password)
            existing.must_change_password = not args.no_force_change
            existing.is_active = True
            action = "Updated"
        db.commit()
    finally:
        db.close()

    print(f"{action} clinician '{username}' (role={args.role}).")
    if args.password is None:
        print(f"Temporary password: {password}")
        print("Share this once; the clinician will be asked to change it on first login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
