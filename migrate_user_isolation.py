"""
One-time migration: add per-user data isolation to an existing database.

Run ONCE with: python migrate_user_isolation.py
"""
from __future__ import annotations

import sys
from sqlalchemy import text, inspect
from app.db import engine


def _db_type() -> str:
    return engine.dialect.name  # "sqlite" or "postgresql"


def run():
    db = _db_type()
    print(f"Database: {db}")

    with engine.connect() as conn:

        # 1. Add user_id column to source_tracks
        insp = inspect(engine)
        existing_cols = [c["name"] for c in insp.get_columns("source_tracks")]
        if "user_id" not in existing_cols:
            conn.execute(text("ALTER TABLE source_tracks ADD COLUMN user_id INTEGER REFERENCES users(id)"))
            conn.commit()
            print("OK - Added user_id column to source_tracks")
        else:
            print("   user_id column already exists, skipping")

        # 2. Assign existing source_tracks to the first admin user
        result = conn.execute(text("SELECT id FROM users WHERE is_admin = 1 OR is_admin = true ORDER BY id LIMIT 1"))
        row = result.fetchone()
        if row:
            admin_id = row[0]
            conn.execute(text("UPDATE source_tracks SET user_id = :uid WHERE user_id IS NULL"), {"uid": admin_id})
            conn.commit()
            print(f"OK - Assigned existing tracks to admin user id={admin_id}")
        else:
            print("   No admin user found - source_tracks left with user_id=NULL")

        # 3. Handle unique constraint - SQLite vs PostgreSQL differ
        if db == "sqlite":
            # SQLite: create a unique index instead (can't ALTER TABLE for constraints)
            existing_indexes = [idx["name"] for idx in insp.get_indexes("source_tracks")]
            if "uq_source_track_user" not in existing_indexes:
                try:
                    conn.execute(text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_source_track_user "
                        "ON source_tracks (source, source_track_id, user_id)"
                    ))
                    conn.commit()
                    print("OK - Created unique index (source, source_track_id, user_id)")
                except Exception as e:
                    print(f"   Could not create unique index: {e}")
            else:
                print("   Unique index already exists, skipping")
        else:
            # PostgreSQL: drop old constraint, add new one
            try:
                conn.execute(text("ALTER TABLE source_tracks DROP CONSTRAINT IF EXISTS uq_source_source_track_id"))
                conn.commit()
                print("OK - Dropped old unique constraint")
            except Exception as e:
                print(f"   Could not drop old constraint: {e}")
            try:
                conn.execute(text(
                    "ALTER TABLE source_tracks ADD CONSTRAINT uq_source_track_user "
                    "UNIQUE (source, source_track_id, user_id)"
                ))
                conn.commit()
                print("OK - Added new unique constraint")
            except Exception as e:
                if "already exists" in str(e).lower():
                    print("   New constraint already exists, skipping")
                else:
                    print(f"   Could not add new constraint: {e}")

        # 4. Create index on user_id
        try:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_source_tracks_user_id ON source_tracks(user_id)"
            ))
            conn.commit()
            print("OK - Created index on source_tracks.user_id")
        except Exception as e:
            print(f"   Could not create index: {e}")

        # 5. Create user_settings table
        tables = insp.get_table_names()
        if "user_settings" not in tables:
            if db == "sqlite":
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS user_settings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                        soundcloud_oauth_token TEXT,
                        muzpa_sess TEXT,
                        deezer_arl TEXT,
                        download_dir TEXT,
                        download_full_eps INTEGER NOT NULL DEFAULT 0,
                        organize_by_like_date INTEGER NOT NULL DEFAULT 0,
                        spotify_token_json TEXT,
                        youtube_token_json TEXT
                    )
                """))
            else:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS user_settings (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                        soundcloud_oauth_token TEXT,
                        muzpa_sess TEXT,
                        deezer_arl TEXT,
                        download_dir TEXT,
                        download_full_eps BOOLEAN NOT NULL DEFAULT FALSE,
                        organize_by_like_date BOOLEAN NOT NULL DEFAULT FALSE,
                        spotify_token_json TEXT,
                        youtube_token_json TEXT
                    )
                """))
            conn.commit()
            print("OK - Created user_settings table")
        else:
            print("   user_settings table already exists, skipping")

        # 6. Migrate existing .env values into admin UserSettings
        from pathlib import Path
        env_path = Path(".env")
        env_vals: dict[str, str] = {}
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env_vals[k.strip()] = v.strip()

        result = conn.execute(text("SELECT id FROM users WHERE is_admin = 1 OR is_admin = true ORDER BY id LIMIT 1"))
        row = result.fetchone()
        if row and env_vals:
            admin_id = row[0]
            # Check if settings row already exists
            existing = conn.execute(text("SELECT id FROM user_settings WHERE user_id = :uid"), {"uid": admin_id}).fetchone()
            if not existing:
                sc_token  = env_vals.get("SOUNDCLOUD_OAUTH_TOKEN", "")
                muzpa     = env_vals.get("MUZPA_SESS", "")
                deezer    = env_vals.get("DEEZER_ARL", "")
                dl_dir    = env_vals.get("DOWNLOAD_DIR", "")
                full_eps  = 1 if env_vals.get("DOWNLOAD_FULL_EPS", "false").lower() == "true" else 0
                by_date   = 1 if env_vals.get("ORGANIZE_BY_LIKE_DATE", "false").lower() == "true" else 0

                conn.execute(text("""
                    INSERT INTO user_settings
                        (user_id, soundcloud_oauth_token, muzpa_sess, deezer_arl,
                         download_dir, download_full_eps, organize_by_like_date)
                    VALUES (:uid, :sc, :muzpa, :deezer, :dl, :eps, :bydate)
                """), {
                    "uid": admin_id, "sc": sc_token, "muzpa": muzpa,
                    "deezer": deezer, "dl": dl_dir, "eps": full_eps, "bydate": by_date,
                })
                conn.commit()
                print(f"OK - Migrated .env settings into UserSettings for admin id={admin_id}")
            else:
                print("   UserSettings row already exists for admin, skipping .env migration")

    print("\nMigration complete. Restart the app.")


if __name__ == "__main__":
    run()
