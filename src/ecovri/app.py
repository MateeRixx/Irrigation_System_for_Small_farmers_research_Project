import os
import io
import csv
import json
import sqlite3
import time
from datetime import datetime, timezone
from functools import wraps

import cv2
import numpy as np
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, jsonify, send_file, send_from_directory, abort, Response
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

from .zoning_engine import process_image, fetch_forecast, fetch_satellite_image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
DATA_DIR = os.environ.get("ECOVRI_DATA_DIR") or os.path.join(PROJECT_ROOT, "data")
UPLOAD_DIR = os.environ.get("ECOVRI_UPLOADS") or os.path.join(DATA_DIR, "uploads")
RESULT_DIR = os.environ.get("ECOVRI_RESULTS") or os.path.join(DATA_DIR, "results")
DB_PATH = os.environ.get("ECOVRI_DB") or os.path.join(DATA_DIR, "irrigation.db")
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(RESULT_DIR, exist_ok=True)

app = Flask(__name__)
APP_ENV = os.environ.get("APP_ENV", "development").lower()
secret_key = os.environ.get("SECRET_KEY")
if APP_ENV == "production" and not secret_key:
    raise RuntimeError("SECRET_KEY must be set when APP_ENV=production")
app.secret_key = secret_key or "ecovri-dev-secret-change-in-production"
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=APP_ENV == "production",
)
if os.environ.get("TRUST_PROXY", "").lower() in {"1", "true", "yes"}:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# In-memory login throttling: ip -> [timestamps]
_LOGIN_ATTEMPTS = {}
_LOGIN_WINDOW = 300
_LOGIN_MAX = 10


def utc_now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS farms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            lat REAL DEFAULT 26.9157,
            lon REAL DEFAULT 70.9083,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            farm_id INTEGER,
            user_id INTEGER NOT NULL,
            image_path TEXT,
            zone_map_path TEXT,
            k INTEGER,
            auto_k INTEGER,
            rain_mm REAL,
            recommendations TEXT,
            wcss TEXT,
            k_range TEXT,
            silhouette REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (farm_id) REFERENCES farms(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS moisture_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zone_id INTEGER NOT NULL,
            farm_id INTEGER NOT NULL,
            moisture REAL NOT NULL,
            recorded_at TEXT NOT NULL
        );
    """)
    try:
        conn.execute("ALTER TABLE analyses ADD COLUMN silhouette REAL")
    except sqlite3.OperationalError:
        pass

    # Legacy schema: farm_id was NOT NULL — standalone analyses need it nullable
    cols = conn.execute("PRAGMA table_info(analyses)").fetchall()
    farm_col = next((c for c in cols if c["name"] == "farm_id"), None)
    if farm_col is not None and farm_col["notnull"]:
        conn.executescript("""
            CREATE TABLE analyses_tmp (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                farm_id INTEGER,
                user_id INTEGER NOT NULL,
                image_path TEXT, zone_map_path TEXT,
                k INTEGER, auto_k INTEGER, rain_mm REAL,
                recommendations TEXT, wcss TEXT, k_range TEXT,
                silhouette REAL, created_at TEXT NOT NULL,
                FOREIGN KEY (farm_id) REFERENCES farms(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            INSERT INTO analyses_tmp
                (id, farm_id, user_id, image_path, zone_map_path, k, auto_k,
                 rain_mm, recommendations, wcss, k_range, silhouette, created_at)
            SELECT id, farm_id, user_id, image_path, zone_map_path, k, auto_k,
                   rain_mm, recommendations, wcss, k_range, silhouette, created_at
            FROM analyses;
            DROP TABLE analyses;
            ALTER TABLE analyses_tmp RENAME TO analyses;
        """)
    conn.commit()
    conn.close()


init_db()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def current_user():
    if "user_id" not in session:
        return None
    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()
    conn.close()
    return user


# ---------------------------------------------------------------------------
# Security helpers — CSRF (forms) + login throttling
# ---------------------------------------------------------------------------
def _ensure_csrf():
    if "csrf_token" not in session:
        session["csrf_token"] = os.urandom(16).hex()
    return session["csrf_token"]


def csrf_protect():
    """Reject state-changing form posts without a valid session token."""
    if request.method != "POST":
        return None
    if request.is_json:
        return None
    token = request.form.get("csrf_token", "")
    if not token or token != session.get("csrf_token"):
        flash("Session expired or invalid request. Please try again.", "danger")
        return redirect(request.referrer or url_for("index"))
    return None


def _login_throttled(ip):
    now = time.time()
    hits = [t for t in _LOGIN_ATTEMPTS.get(ip, []) if now - t < _LOGIN_WINDOW]
    _LOGIN_ATTEMPTS[ip] = hits
    return len(hits) >= _LOGIN_MAX


def _record_login_attempt(ip):
    _LOGIN_ATTEMPTS.setdefault(ip, []).append(time.time())


@app.context_processor
def inject_globals():
    return {"csrf_token": _ensure_csrf()}


@app.errorhandler(404)
def not_found(_e):
    return render_template("404.html"), 404


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200


@app.route("/media/<path:filename>")
@login_required
def media(filename):
    """Serve generated media from local or persistent deployment storage."""
    if filename.startswith("uploads/"):
        return send_from_directory(UPLOAD_DIR, filename.removeprefix("uploads/"))
    if filename.startswith("results/"):
        return send_from_directory(RESULT_DIR, filename.removeprefix("results/"))
    abort(404)


@app.errorhandler(500)
def server_error(_e):
    return render_template("500.html"), 500


@app.route("/methodology")
def methodology():
    return render_template("methodology.html")


# ---------------------------------------------------------------------------
# Routes — Auth
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        denied = csrf_protect()
        if denied:
            return denied
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not username or not email or not password:
            flash("All fields are required.", "danger")
            return render_template("register.html")
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("register.html")

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO users (username, email, password_hash, created_at) "
                "VALUES (?, ?, ?, ?)",
                (username, email, generate_password_hash(password),
                 utc_now()),
            )
            conn.commit()
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username or email already exists.", "danger")
        finally:
            conn.close()
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        denied = csrf_protect()
        if denied:
            return denied
        ip = request.remote_addr or "unknown"
        if _login_throttled(ip):
            flash("Too many login attempts. Try again in a few minutes.", "danger")
            return render_template("login.html"), 429
        _record_login_attempt(ip)
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? OR email = ?",
            (username, username),
        ).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            flash(f"Welcome back, {user['username']}!", "success")
            nxt = request.args.get("next")
            return redirect(nxt or url_for("dashboard"))
        flash("Invalid credentials.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Routes — Dashboard & Farms
# ---------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    uid = session["user_id"]
    conn = get_db()
    farms = conn.execute(
        "SELECT * FROM farms WHERE user_id = ? ORDER BY id DESC", (uid,)
    ).fetchall()
    analyses = conn.execute(
        "SELECT a.*, f.name AS farm_name FROM analyses a "
        "LEFT JOIN farms f ON a.farm_id = f.id "
        "WHERE a.user_id = ? ORDER BY a.id DESC LIMIT 20", (uid,)
    ).fetchall()
    conn.close()
    return render_template("dashboard.html", farms=farms, analyses=analyses)


@app.route("/farms/add", methods=["POST"])
@login_required
def add_farm():
    denied = csrf_protect()
    if denied:
        return denied
    name = request.form.get("name", "").strip()
    try:
        lat = float(request.form.get("lat", 26.9157) or 26.9157)
        lon = float(request.form.get("lon", 70.9083) or 70.9083)
    except (TypeError, ValueError):
        flash("Latitude and longitude must be valid numbers.", "danger")
        return redirect(url_for("dashboard"))
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        flash("Latitude or longitude is outside its valid range.", "danger")
        return redirect(url_for("dashboard"))
    if not name:
        flash("Farm name is required.", "danger")
        return redirect(url_for("dashboard"))
    conn = get_db()
    conn.execute(
        "INSERT INTO farms (user_id, name, lat, lon, created_at) VALUES (?, ?, ?, ?, ?)",
        (session["user_id"], name, lat, lon, utc_now()),
    )
    conn.commit()
    conn.close()
    flash("Farm added.", "success")
    return redirect(url_for("dashboard"))


@app.route("/farms/<int:farm_id>")
@login_required
def farm_detail(farm_id):
    conn = get_db()
    farm = conn.execute(
        "SELECT * FROM farms WHERE id = ? AND user_id = ?",
        (farm_id, session["user_id"]),
    ).fetchone()
    if not farm:
        conn.close()
        abort(404)
    analyses = conn.execute(
        "SELECT * FROM analyses WHERE farm_id = ? ORDER BY id DESC", (farm_id,)
    ).fetchall()
    conn.close()
    return render_template("farm.html", farm=farm, analyses=analyses)


def _save_analysis(uid, farm_id, img, result):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fname_in = f"in_{stamp}_{uid}.jpg"
    fname_out = f"zone_{stamp}_{uid}.jpg"
    cv2.imwrite(os.path.join(UPLOAD_DIR, fname_in), img)
    cv2.imwrite(os.path.join(RESULT_DIR, fname_out), result["zone_map_bgr"])
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO analyses (farm_id, user_id, image_path, zone_map_path, "
            "k, auto_k, rain_mm, recommendations, wcss, k_range, silhouette, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                farm_id, uid,
                f"uploads/{fname_in}", f"results/{fname_out}",
                result["k"], result["auto_k"], result["rain_mm"],
                json.dumps(result["recommendations"]),
                json.dumps(result["wcss"]),
                json.dumps(result["k_range"]),
                result.get("silhouette"),
                utc_now(),
            ),
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def _resolve_farm(uid, farm_id):
    """Return (farm_id, lat, lon) validating ownership; (None, lat, lon) if 0/invalid."""
    lat, lon = 26.9157, 70.9083
    if not farm_id:
        return None, lat, lon
    conn = get_db()
    farm = conn.execute(
        "SELECT * FROM farms WHERE id = ? AND user_id = ?", (farm_id, uid)
    ).fetchone()
    conn.close()
    if farm:
        return farm["id"], farm["lat"], farm["lon"]
    return None, lat, lon


# ---------------------------------------------------------------------------
# Routes — Analysis (image upload + zoning)
# ---------------------------------------------------------------------------
@app.route("/analyze", methods=["GET", "POST"])
@login_required
def analyze():
    uid = session["user_id"]
    conn = get_db()
    farms = conn.execute(
        "SELECT * FROM farms WHERE user_id = ? ORDER BY id", (uid,)
    ).fetchall()
    conn.close()

    if request.method == "POST":
        denied = csrf_protect()
        if denied:
            return denied
        if "image" not in request.files:
            flash("No image uploaded.", "danger")
            return redirect(url_for("analyze"))
        file = request.files["image"]
        if file.filename == "":
            flash("No image selected.", "danger")
            return redirect(url_for("analyze"))

        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in ALLOWED_EXT:
            flash("Unsupported file type. Use PNG, JPG, or WEBP.", "danger")
            return redirect(url_for("analyze"))

        try:
            farm_id = int(request.form.get("farm_id", 0) or 0)
        except (TypeError, ValueError):
            flash("Invalid farm selection.", "danger")
            return redirect(url_for("analyze"))
        force_k = request.form.get("force_k", "")
        force_k = (
            int(force_k)
            if force_k.isdigit() and 2 <= int(force_k) <= 9
            else None
        )

        farm_id, lat, lon = _resolve_farm(uid, farm_id)

        raw = np.frombuffer(file.read(), np.uint8)
        img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if img is None:
            flash("Could not read the image.", "danger")
            return redirect(url_for("analyze"))

        try:
            result = process_image(img, lat=lat, lon=lon, force_k=force_k)
        except Exception as e:
            flash(f"Processing failed: {e}", "danger")
            return redirect(url_for("analyze"))

        aid = _save_analysis(uid, farm_id, img, result)
        return redirect(url_for("analysis_detail", analysis_id=aid))

    return render_template("analyze.html", farms=farms)


@app.route("/analyze/map", methods=["POST"])
@login_required
def analyze_map():
    uid = session["user_id"]
    data = request.get_json(force=True, silent=True) or {}
    bbox = data.get("bbox")
    if (not isinstance(bbox, list) or len(bbox) != 4
            or not all(isinstance(v, (int, float)) for v in bbox)):
        return jsonify({"error": "bbox [west, south, east, north] required"}), 400

    west, south, east, north = bbox
    if (east <= west or north <= south or not -180 <= west <= 180
            or not -180 <= east <= 180 or not -90 <= south <= 90
            or not -90 <= north <= 90):
        return jsonify({"error": "Invalid bounding box."}), 400

    try:
        farm_id = int(data.get("farm_id") or 0)
    except (TypeError, ValueError):
        farm_id = 0
    force_k = data.get("force_k")
    force_k = int(force_k) if isinstance(force_k, int) and 2 <= force_k <= 9 else None
    farm_id, _, _ = _resolve_farm(uid, farm_id)
    lat = (south + north) / 2
    lon = (west + east) / 2

    try:
        img = fetch_satellite_image((west, south, east, north))
    except Exception as e:
        return jsonify({"error": f"Could not fetch satellite imagery: {e}"}), 502

    try:
        result = process_image(img, lat=lat, lon=lon, force_k=force_k)
    except Exception as e:
        return jsonify({"error": f"Processing failed: {e}"}), 500

    aid = _save_analysis(uid, farm_id, img, result)
    return jsonify({"ok": True, "analysis_id": aid,
                    "redirect": url_for("analysis_detail", analysis_id=aid)})


@app.route("/analysis/<int:analysis_id>")
@login_required
def analysis_detail(analysis_id):
    conn = get_db()
    row = conn.execute(
        "SELECT a.*, f.name AS farm_name, f.lat, f.lon FROM analyses a "
        "LEFT JOIN farms f ON a.farm_id = f.id "
        "WHERE a.id = ? AND a.user_id = ?",
        (analysis_id, session["user_id"]),
    ).fetchone()
    conn.close()
    if not row:
        abort(404)
    recs = json.loads(row["recommendations"])
    wcss = json.loads(row["wcss"])
    k_range = json.loads(row["k_range"])
    return render_template(
        "analysis.html", a=row, recs=recs, wcss=wcss, k_range=k_range
    )


def _own_analysis(analysis_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM analyses WHERE id = ? AND user_id = ?",
        (analysis_id, session["user_id"]),
    ).fetchone()
    conn.close()
    return row


@app.route("/analysis/<int:analysis_id>/export.csv")
@login_required
def export_csv(analysis_id):
    a = _own_analysis(analysis_id)
    if not a:
        abort(404)
    recs = json.loads(a["recommendations"])
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["zone", "soil", "moisture", "deficit_mm",
                     "water_mm", "minutes", "area_pct"])
    for r in recs:
        writer.writerow([r["zone"], r["soil"], r["moisture"], r["deficit_mm"],
                         r["water_mm"], r["minutes"], r["area_pct"]])
    return Response(
        out.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition":
                 f"attachment; filename=ecovri_analysis_{analysis_id}.csv"},
    )


@app.route("/analysis/<int:analysis_id>/export.json")
@login_required
def export_json(analysis_id):
    a = _own_analysis(analysis_id)
    if not a:
        abort(404)
    payload = {
        "analysis_id": a["id"],
        "farm_id": a["farm_id"],
        "k": a["k"],
        "auto_k": a["auto_k"],
        "rain_7d_mm": a["rain_mm"],
        "silhouette": a["silhouette"],
        "created_at": a["created_at"],
        "zones": json.loads(a["recommendations"]),
    }
    return Response(
        json.dumps(payload, indent=2), mimetype="application/json",
        headers={"Content-Disposition":
                 f"attachment; filename=ecovri_analysis_{analysis_id}.json"},
    )


@app.route("/analysis/<int:analysis_id>/delete", methods=["POST"])
@login_required
def delete_analysis(analysis_id):
    denied = csrf_protect()
    if denied:
        return denied
    a = _own_analysis(analysis_id)
    if not a:
        abort(404)
    conn = get_db()
    conn.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,))
    conn.commit()
    conn.close()
    flash("Analysis deleted.", "success")
    nxt = request.form.get("next") or url_for("dashboard")
    return redirect(nxt)


@app.route("/farms/<int:farm_id>/delete", methods=["POST"])
@login_required
def delete_farm(farm_id):
    denied = csrf_protect()
    if denied:
        return denied
    conn = get_db()
    farm = conn.execute(
        "SELECT * FROM farms WHERE id = ? AND user_id = ?",
        (farm_id, session["user_id"]),
    ).fetchone()
    if not farm:
        conn.close()
        abort(404)
    conn.execute("DELETE FROM farms WHERE id = ?", (farm_id,))
    conn.commit()
    conn.close()
    flash(f"Farm “{farm['name']}” and its analyses deleted.", "success")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Routes — API (for ESP32 / external)
# ---------------------------------------------------------------------------
@app.route("/api/weather")
@login_required
def api_weather():
    try:
        lat = float(request.args.get("lat", 26.9157))
        lon = float(request.args.get("lon", 70.9083))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon must be valid numbers"}), 400
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return jsonify({"error": "lat or lon is outside its valid range"}), 400
    forecast = fetch_forecast(lat, lon)
    return jsonify({
        "lat": lat, "lon": lon,
        "rain_7d_mm": forecast["total_mm"],
        "daily": forecast["days"],
    })


@app.route("/api/zones/<int:farm_id>")
@login_required
def api_zones(farm_id):
    conn = get_db()
    row = conn.execute(
        "SELECT recommendations, k, rain_mm FROM analyses "
        "WHERE farm_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1",
        (farm_id, session["user_id"]),
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "No analysis for this farm"}), 404
    return jsonify({
        "k": row["k"],
        "rain_mm": row["rain_mm"],
        "zones": json.loads(row["recommendations"]),
    })


@app.route("/api/moisture", methods=["GET", "POST"])
@login_required
def api_moisture():
    if request.method == "GET":
        farm_id = request.args.get("farm_id", type=int)
        if not farm_id:
            return jsonify({"error": "farm_id required"}), 400
        conn = get_db()
        farm = conn.execute(
            "SELECT id FROM farms WHERE id = ? AND user_id = ?",
            (farm_id, session["user_id"]),
        ).fetchone()
        if not farm:
            conn.close()
            return jsonify({"error": "Farm not found"}), 404
        rows = conn.execute(
            "SELECT zone_id, moisture, recorded_at FROM moisture_readings "
            "WHERE farm_id = ? ORDER BY id DESC LIMIT 300",
            (farm_id,),
        ).fetchall()
        conn.close()
        readings = [
            {"zone_id": r["zone_id"], "moisture": r["moisture"],
             "recorded_at": r["recorded_at"]}
            for r in reversed(rows)
        ]
        return jsonify({"farm_id": farm_id, "readings": readings})

    data = request.get_json(force=True, silent=True) or {}
    farm_id = data.get("farm_id")
    zone_id = data.get("zone_id")
    moisture = data.get("moisture")
    if farm_id is None or zone_id is None or moisture is None:
        return jsonify({"error": "farm_id, zone_id, moisture required"}), 400
    try:
        farm_id = int(farm_id)
        zone_id = int(zone_id)
        moisture = float(moisture)
    except (TypeError, ValueError):
        return jsonify({"error": "farm_id, zone_id, and moisture must be numeric"}), 400
    if farm_id < 1 or zone_id < 1 or not 0 <= moisture <= 1:
        return jsonify({
            "error": "farm_id and zone_id must be positive; moisture must be 0..1"
        }), 400
    conn = get_db()
    farm = conn.execute(
        "SELECT id FROM farms WHERE id = ? AND user_id = ?",
        (farm_id, session["user_id"]),
    ).fetchone()
    if not farm:
        conn.close()
        return jsonify({"error": "Farm not found"}), 404
    conn.execute(
        "INSERT INTO moisture_readings (zone_id, farm_id, moisture, recorded_at) "
        "VALUES (?, ?, ?, ?)",
        (zone_id, farm_id, moisture, utc_now()),
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"}), 201


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=debug, host=host, port=port)
