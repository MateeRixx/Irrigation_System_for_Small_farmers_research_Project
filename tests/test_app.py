"""End-to-end test suite for Smart Eco-VRI.

Run:  pytest tests/ -v
All external network calls (Esri, Open-Meteo) are mocked.
"""
import io
import os
import sys
import tempfile

import cv2
import numpy as np
import pytest

# --- configure isolated environment BEFORE importing the app ----------------
_TMP = tempfile.mkdtemp(prefix="ecovri_test_")
os.environ.setdefault("ECOVRI_DB", os.path.join(_TMP, "test.db"))
os.environ.setdefault("ECOVRI_UPLOADS", os.path.join(_TMP, "uploads"))
os.environ.setdefault("ECOVRI_RESULTS", os.path.join(_TMP, "results"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
from app import app, get_db  # noqa: E402
import zoning_engine  # noqa: E402

app.config["TESTING"] = True
CSRF = "test-csrf-token"


# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------
def synthetic_bgr(w=240, h=160):
    """Three-band test image so K-Means finds distinct clusters."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, : w // 3] = (40, 180, 40)
    img[:, w // 3: 2 * w // 3] = (30, 30, 200)
    img[:, 2 * w // 3:] = (200, 120, 40)
    noise = np.random.default_rng(0).normal(0, 8, img.shape)
    return cv2.add(img, noise.astype(np.uint8))


def image_file(img=None):
    img = img if img is not None else synthetic_bgr()
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return io.BytesIO(buf.tobytes())


@pytest.fixture(autouse=True)
def clean_state():
    appmod._LOGIN_ATTEMPTS.clear()
    conn = get_db()
    conn.execute("DELETE FROM moisture_readings")
    conn.execute("DELETE FROM analyses")
    conn.execute("DELETE FROM farms")
    conn.execute("DELETE FROM users")
    # AUTOINCREMENT never reuses ids — reset so tests can rely on id=1
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
    conn.close()
    yield


@pytest.fixture
def client():
    c = app.test_client()
    with c.session_transaction() as s:
        s["csrf_token"] = CSRF  # seed BEFORE any POST so csrf_protect passes
    return c


@pytest.fixture
def logged_in(client):
    client.post("/register", data={
        "username": "tester", "email": "t@example.com",
        "password": "secret1", "csrf_token": CSRF,
    }, follow_redirects=True)
    client.post("/login", data={
        "username": "tester", "password": "secret1", "csrf_token": CSRF,
    }, follow_redirects=True)
    with client.session_transaction() as s:
        assert s.get("user_id"), "login must have succeeded"
    return client


@pytest.fixture
def mock_network(monkeypatch):
    """No internet in tests: stub every outbound call."""
    monkeypatch.setattr(zoning_engine, "fetch_rain", lambda lat, lon: 4.2)
    monkeypatch.setattr(appmod, "fetch_forecast", lambda lat, lon, days=7: {
        "total_mm": 4.2,
        "days": [{"date": "2026-01-01", "mm": 1.2}, {"date": "2026-01-02", "mm": 3.0}],
    })
    monkeypatch.setattr(appmod, "fetch_satellite_image",
                        lambda bbox, max_dim=1400: synthetic_bgr())


def _count(table):
    conn = get_db()
    n = conn.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]
    conn.close()
    return n


# ---------------------------------------------------------------------------
# Pages & auth
# ---------------------------------------------------------------------------
def test_landing_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Draw irrigation zones" in r.data
    assert b"hero-zones" in r.data


def test_health_endpoint_is_public(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


def test_register_login_logout(client):
    r = client.post("/register", data={
        "username": "alice", "email": "a@x.io", "password": "pass123",
        "csrf_token": CSRF,
    }, follow_redirects=True)
    assert b"Account created" in r.data

    r = client.post("/login", data={
        "username": "alice", "password": "pass123", "csrf_token": CSRF,
    }, follow_redirects=True)
    assert b"Dashboard" in r.data

    client.get("/logout", follow_redirects=True)
    r = client.get("/dashboard", follow_redirects=True)
    assert b"Welcome back" in r.data


def test_auth_guard_blocks_app(client):
    r = client.get("/dashboard", follow_redirects=True)
    assert b"Welcome back" in r.data
    r = client.post("/analyze/map", json={"bbox": [1, 1, 2, 2]})
    assert r.status_code == 302


def test_csrf_rejected(logged_in):
    r = logged_in.post("/farms/add",
                       data={"name": "Nope", "csrf_token": "forged"},
                       follow_redirects=True)
    assert b"Session expired" in r.data
    assert _count("farms") == 0


def test_invalid_numeric_inputs_return_validation_errors(logged_in):
    response = logged_in.post("/farms/add", data={
        "name": "Bad coordinates", "lat": "not-a-number",
        "lon": "70.9", "csrf_token": CSRF,
    })
    assert response.status_code == 302
    assert _count("farms") == 0

    assert logged_in.get("/api/weather?lat=invalid").status_code == 400
    response = logged_in.post("/api/moisture", json={
        "farm_id": "bad", "zone_id": 1, "moisture": 0.2,
    })
    assert response.status_code == 400


def test_login_throttled_after_10_attempts(client):
    for _ in range(10):
        client.post("/login", data={
            "username": "x", "password": "y", "csrf_token": CSRF})
    r = client.post("/login", data={
        "username": "x", "password": "y", "csrf_token": CSRF})
    assert r.status_code == 429


# ---------------------------------------------------------------------------
# Farms & analyses
# ---------------------------------------------------------------------------
def test_farm_crud_and_cascade(logged_in, mock_network):
    logged_in.post("/farms/add", data={
        "name": "Test Plot", "lat": "26.9", "lon": "70.9", "csrf_token": CSRF,
    }, follow_redirects=True)
    assert _count("farms") == 1

    r = logged_in.post("/analyze/map", json={
        "bbox": [70.85, 26.85, 70.87, 26.87], "farm_id": 1, "force_k": 3,
    })
    assert r.status_code == 200
    assert _count("analyses") == 1

    r = logged_in.post("/farms/1/delete", data={"csrf_token": CSRF},
                       follow_redirects=True)
    assert r.status_code == 200
    assert _count("farms") == 0
    assert _count("analyses") == 0, "analyses must cascade with their farm"


def test_upload_analysis(logged_in, mock_network):
    r = logged_in.post("/analyze", data={
        "image": (image_file(), "field.jpg"),
        "farm_id": "0", "force_k": "3", "csrf_token": CSRF,
    }, content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    assert b"Irrigation recommendations" in r.data
    assert b"Silhouette" in r.data


def test_map_analysis_and_silhouette_stored(logged_in, mock_network):
    r = logged_in.post("/analyze/map", json={
        "bbox": [70.85, 26.85, 70.87, 26.87], "force_k": 3,
    })
    assert r.status_code == 200
    aid = r.get_json()["analysis_id"]

    conn = get_db()
    row = conn.execute(
        "SELECT silhouette, k FROM analyses WHERE id=?", (aid,)
    ).fetchone()
    conn.close()
    assert row["k"] == 3
    assert row["silhouette"] is not None and 0 <= row["silhouette"] <= 1

    page = logged_in.get(f"/analysis/{aid}")
    assert page.status_code == 200
    assert b"Elbow method" in page.data
    assert b"Z1" in page.data


def test_map_validation_errors(logged_in):
    assert logged_in.post("/analyze/map", json={}).status_code == 400
    assert logged_in.post(
        "/analyze/map", json={"bbox": [70.9, 26.9, 70.8, 26.8]}
    ).status_code == 400


def test_delete_analysis(logged_in, mock_network):
    r = logged_in.post("/analyze/map", json={"bbox": [70.1, 26.1, 70.2, 26.2]})
    assert r.status_code == 200
    aid = r.get_json()["analysis_id"]
    logged_in.post(f"/analysis/{aid}/delete", data={"csrf_token": CSRF},
                   follow_redirects=True)
    assert _count("analyses") == 0


def test_ownership_isolation(logged_in, mock_network):
    r = logged_in.post("/analyze/map", json={"bbox": [70.1, 26.1, 70.2, 26.2]})
    aid = r.get_json()["analysis_id"]

    other = app.test_client()
    with other.session_transaction() as s:
        s["csrf_token"] = CSRF
    other.post("/register", data={
        "username": "other", "email": "o@x.io", "password": "pass123",
        "csrf_token": CSRF,
    }, follow_redirects=True)
    other.post("/login", data={
        "username": "other", "password": "pass123", "csrf_token": CSRF,
    }, follow_redirects=True)

    assert other.get(f"/analysis/{aid}").status_code == 404
    assert other.get(f"/analysis/{aid}/export.csv").status_code == 404
    assert other.post(f"/analysis/{aid}/delete",
                      data={"csrf_token": CSRF}).status_code == 404


# ---------------------------------------------------------------------------
# Exports, APIs, pages
# ---------------------------------------------------------------------------
def test_export_csv_and_json(logged_in, mock_network):
    r = logged_in.post("/analyze/map", json={
        "bbox": [70.1, 26.1, 70.2, 26.2], "force_k": 3})
    aid = r.get_json()["analysis_id"]

    csv_r = logged_in.get(f"/analysis/{aid}/export.csv")
    assert csv_r.status_code == 200
    assert csv_r.mimetype == "text/csv"
    body = csv_r.data.decode()
    assert body.splitlines()[0].startswith("zone,soil,moisture")
    assert len(body.splitlines()) == 4  # header + 3 zones

    json_r = logged_in.get(f"/analysis/{aid}/export.json")
    assert json_r.status_code == 200
    payload = json_r.get_json()
    assert payload["k"] == 3 and len(payload["zones"]) == 3
    assert payload["silhouette"] is not None


def test_weather_api_returns_daily(logged_in, mock_network):
    r = logged_in.get("/api/weather?lat=26.9&lon=70.9")
    assert r.status_code == 200
    j = r.get_json()
    assert j["rain_7d_mm"] == 4.2
    assert len(j["daily"]) == 2


def test_zones_api(logged_in, mock_network):
    logged_in.post("/farms/add", data={"name": "P", "csrf_token": CSRF})
    r = logged_in.post("/analyze/map", json={
        "bbox": [70.1, 26.1, 70.2, 26.2], "farm_id": 1, "force_k": 3})
    assert r.status_code == 200
    r = logged_in.get("/api/zones/1")
    assert r.status_code == 200
    assert len(r.get_json()["zones"]) == 3


def test_moisture_post_get_roundtrip(logged_in, mock_network):
    logged_in.post("/farms/add", data={"name": "P", "csrf_token": CSRF})
    r = logged_in.post("/api/moisture", json={
        "farm_id": 1, "zone_id": 2, "moisture": 0.27})
    assert r.status_code == 201

    r = logged_in.get("/api/moisture?farm_id=1")
    assert r.status_code == 200
    readings = r.get_json()["readings"]
    assert len(readings) == 1 and readings[0]["zone_id"] == 2

    assert logged_in.get("/api/moisture").status_code == 400
    assert logged_in.get("/api/moisture?farm_id=99").status_code == 404


def test_error_and_methodology_pages(client):
    assert client.get("/methodology").status_code == 200
    r = client.get("/definitely-missing")
    assert r.status_code == 404
    assert b"404" in r.data and b"not found" in r.data.lower()
