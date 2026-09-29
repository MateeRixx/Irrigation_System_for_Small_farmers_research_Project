# 🌱 Smart Eco-VRI: Low-Cost Variable Rate Irrigation System

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![Flask](https://img.shields.io/badge/Flask-3.0-000?logo=flask)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Status](https://img.shields.io/badge/Status-Working-brightgreen)

> **A satellite-driven, IoT-enabled precision irrigation web platform for smallholder farmers (<5 acres).**

---

## 📖 Overview

**Smart Eco-VRI** is a full-stack research project that turns traditional irrigation into an intelligent, data-driven network for under **$90 USD** in hardware.

The platform uses **free satellite imagery** and **unsupervised ML (K-Means + Elbow Method)** to identify the minimum number of irrigation zones for a field, then generates per-zone watering recommendations using a dynamic soil-tank water balance model with live weather integration.

### 🎯 Key Features

* **Web Dashboard** — register / login, manage farms, run analyses, view history.
* **Satellite Zoning** — upload a field image; VARI index + K-Means clustering with automatic elbow-based *k* selection.
* **Irrigation Recommendations** — per-zone soil type, moisture, deficit, water volume, run-time, and area share.
* **Elbow Chart** — rendered live in the browser showing WCSS vs *k*.
* **Weather Aware** — 7-day precipitation forecast via Open-Meteo; large rain totals trigger a skip-irrigation warning.
* **REST API** — JSON endpoints for zones, weather, and soil-moisture ingestion (designed for ESP32 / mobile clients).

---

## 🚀 Quick Start

```bash
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000**, register an account, then run an analysis.

### Using the app

1. **Register / Log in** — create your account.
2. **Add a farm** — name + GPS coordinates.
3. **New Analysis** — upload a satellite image (PNG/JPG), optionally force *k*, link to a farm.
4. **View result** — zone map, elbow chart, and the irrigation recommendation table.
5. **API** — point your ESP32 at `/api/zones/<farm_id>` or `POST /api/moisture`.

For local development, `app.py` listens on `127.0.0.1:5000` with debug mode
off by default. Set `FLASK_DEBUG=1` only on a local machine.

The application data is written to `data/` by default. This keeps the working
tree clean and makes it easy to mount one persistent directory in deployment.

## ☁️ Production Deployment

The repository includes [`wsgi.py`](./wsgi.py) and a [`Procfile`](./Procfile)
for Linux WSGI hosts such as Render, Railway, or a VM.

### Render (recommended)

1. Push the repository to GitHub and create a **Web Service** from it.
2. Use these settings:
   * **Build command:** `pip install -r requirements.txt`
   * **Start command:** `gunicorn --workers 2 --timeout 120 --access-logfile - --bind 0.0.0.0:$PORT wsgi:app`
   * **Health check path:** `/healthz`
3. Add environment variables:
   * `APP_ENV=production`
   * `SECRET_KEY` — a long random value (for example,
     `python -c "import secrets; print(secrets.token_urlsafe(32))"`)
   * `TRUST_PROXY=true`
4. Attach a persistent disk mounted at `/var/data`, then add:
   * `ECOVRI_DATA_DIR=/var/data`
5. Deploy. The application creates its SQLite tables and upload directories on
   first start.

The database and generated images are local files. Without persistent storage,
they will be lost when the service is redeployed or restarted. For multiple
web workers or multiple instances, migrate the database to PostgreSQL and move
uploads/results to object storage before scaling horizontally.

### Any Linux host

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export APP_ENV=production
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export TRUST_PROXY=true
gunicorn --workers 2 --timeout 120 --access-logfile - --bind 0.0.0.0:8000 wsgi:app
```

Put Nginx or another TLS reverse proxy in front of Gunicorn. Do not expose
Flask's development server to the public internet. See
[`.env.example`](./.env.example) for all supported deployment variables.

### Demo data

`scripts/seed_demo.py` is for local demonstrations only and creates the predictable
`admin` / `admin123` account. Do not run it on a public production service.

### Docker (alternative)

Docker is the most reproducible deployment option. Copy `.env.example` to
`.env`, replace `SECRET_KEY`, then run:

```bash
docker compose up --build -d
```

Open **http://localhost:8000**. The named `ecovri_data` volume persists the
SQLite database and generated images. Check the container with:

```bash
docker compose ps
docker compose logs -f web
```

Stop it with `docker compose down` (the named data volume is retained).

---

## 🔌 API Endpoints

| Method | Endpoint | Auth | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/weather?lat=&lon=` | ✅ | 7-day rain total (mm) |
| `GET` | `/api/zones/<farm_id>` | ✅ | Latest zone recommendations |
| `POST` | `/api/moisture` | ✅ | Ingest a soil-moisture reading |

---

## 🧠 How It Works

1. **VARI index** — `VARI = (G − R) / (G + R − B)` computed per pixel from the uploaded RGB image.
2. **Elbow method** — K-Means fitted for *k* = 1…9; the point of maximum distance from the chord selects the optimal *k*.
3. **Clustering** — pixels are grouped into *k* zones; each cluster centre maps to a soil class (sandy / loamy / clayey).
4. **Water balance** — deficit = (field capacity − moisture) × root depth; net need = deficit − forecasted rain.
5. **Run time** — `minutes = water_mm / 15 mm·h⁻¹ × 60`.

---

## 📂 Repository Structure

```
├── src/ecovri/           # Installable application package
│   ├── app.py            # Flask app, auth, DB, routes, API
│   ├── zoning_engine.py  # Core ML pipeline (VARI, elbow, clustering)
│   ├── templates/        # Jinja2 pages
│   └── static/           # Browser assets
├── app.py                # Compatibility entrypoint: python app.py
├── zoning_engine.py      # Compatibility import shim
├── wsgi.py               # Gunicorn/WSGI entrypoint
├── data/                 # Runtime database/uploads/results (ignored)
├── requirements.txt
├── Dockerfile            # Reproducible production image
├── docker-compose.yml    # Local/server deployment with persistent volume
├── render.yaml           # Render service + persistent disk definition
├── scripts/              # Demo and legacy research scripts
└── tests/                # Automated application tests
```

---

## 🗄️ Data Model

| Table | Purpose |
| :--- | :--- |
| `users` | Account credentials (hashed passwords) |
| `farms` | Named fields with GPS coordinates |
| `analyses` | Stored zone-map images + recommendations JSON |
| `moisture_readings` | Time-series soil-moisture from field sensors |

---

## 🔒 Auth & Security

* Passwords hashed with Werkzeug `generate_password_hash`.
* Session-based login via signed cookies.
* All farm / analysis queries are scoped to the logged-in user.
* Upload size capped at 16 MB; only PNG/JPG/WEBP accepted.

---

## ⚙️ Hardware (Phase 2 — ESP32)

| Component | Function | Approx Cost |
| :--- | :--- | :--- |
| ESP32 DevKit V1 | Main controller (Wi-Fi) | $6.00 |
| Capacitive soil sensors | Moisture reading | $2.00 each |
| 4-channel relay module | Valve control | $4.00 |
| 12V solenoid valves | Water actuation | $10.00 each |
| 12V power supply | Power | $10.00 |

The ESP32 polls `/api/zones/<farm_id>` for its schedule and posts readings to `POST /api/moisture`.

---

## 📜 License

MIT — see [LICENSE](LICENSE).
