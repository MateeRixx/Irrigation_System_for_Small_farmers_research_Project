"""Idempotent demo seeder: admin account + farm + one finished analysis.

Usage:  python scripts/seed_demo.py
Safe to run repeatedly.
"""
import json
import os
import sys
import cv2
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

from ecovri.app import app, get_db, _save_analysis
from ecovri.zoning_engine import process_image
from werkzeug.security import generate_password_hash

DEMO_USER = "admin"
DEMO_PASS = "admin123"
DEMO_EMAIL = "admin@ecovri.io"
DEMO_FARM = ("Jais Farm", 26.9157, 70.9083)
SAMPLE_IMAGE = os.path.join(PROJECT_ROOT, "jais_farm.jpg")


def seed():
    with app.app_context():
        conn = get_db()

        user = conn.execute(
            "SELECT id FROM users WHERE username = ?", (DEMO_USER,)
        ).fetchone()
        if user:
            uid = user["id"]
            print(f"User '{DEMO_USER}' already exists (id={uid}).")
        else:
            cur = conn.execute(
                "INSERT INTO users (username, email, password_hash, created_at) "
                "VALUES (?, ?, ?, ?)",
                (DEMO_USER, DEMO_EMAIL, generate_password_hash(DEMO_PASS),
                 datetime.utcnow().isoformat()),
            )
            uid = cur.lastrowid
            conn.commit()
            print(f"Created user '{DEMO_USER}' / '{DEMO_PASS}' (id={uid}).")

        farm = conn.execute(
            "SELECT id FROM farms WHERE user_id = ? AND name = ?",
            (uid, DEMO_FARM[0]),
        ).fetchone()
        if farm:
            fid = farm["id"]
            print(f"Farm '{DEMO_FARM[0]}' already exists (id={fid}).")
        else:
            cur = conn.execute(
                "INSERT INTO farms (user_id, name, lat, lon, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (uid, DEMO_FARM[0], DEMO_FARM[1], DEMO_FARM[2],
                 datetime.utcnow().isoformat()),
            )
            fid = cur.lastrowid
            conn.commit()
            print(f"Created farm '{DEMO_FARM[0]}' (id={fid}).")

        existing = conn.execute(
            "SELECT COUNT(*) AS n FROM analyses WHERE user_id = ?", (uid,)
        ).fetchone()["n"]
        conn.close()

        if existing:
            print(f"User already has {existing} analyses — skipping analysis seed.")
            return

        if not os.path.exists(SAMPLE_IMAGE):
            print(f"Sample image missing ({SAMPLE_IMAGE}) — skipping analysis seed.")
            return

        print("Running zoning on jais_farm.jpg (runs elbow + K-Means)…")
        img = cv2.imread(SAMPLE_IMAGE)
        result = process_image(img, lat=DEMO_FARM[1], lon=DEMO_FARM[2], force_k=3)
        aid = _save_analysis(uid, fid, img, result)
        print(f"Seeded analysis id={aid}: k={result['k']}, "
              f"silhouette={result['silhouette']}, rain={result['rain_mm']}mm")


if __name__ == "__main__":
    seed()
    print("Done. Log in with admin / admin123")
