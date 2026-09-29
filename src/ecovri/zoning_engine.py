import numpy as np
import cv2
from sklearn.cluster import KMeans
import urllib.request
import json


def compute_vari(img_rgb):
    R = img_rgb[:, :, 0].astype(float)
    G = img_rgb[:, :, 1].astype(float)
    B = img_rgb[:, :, 2].astype(float)
    numerator = G - R
    denominator = G + R - B + 0.0001
    vari_img = numerator / denominator
    vari_img = np.nan_to_num(vari_img, nan=0.0, posinf=1.0, neginf=0.0).astype(np.float32)
    return cv2.normalize(vari_img, None, 0, 1, cv2.NORM_MINMAX)


def elbow_optimal_k(sample_pixels, k_max=9):
    wcss = []
    K_range = range(1, k_max + 1)
    for k in K_range:
        km = KMeans(n_clusters=k, init='k-means++', random_state=42, n_init=10)
        km.fit(sample_pixels)
        wcss.append(km.inertia_)

    ks = np.array(list(K_range), dtype=float)
    y = np.array(wcss, dtype=float)
    p1 = np.array([ks[0], y[0]])
    p2 = np.array([ks[-1], y[-1]])
    v = p2 - p1
    n = np.linalg.norm(v)
    if n == 0:
        return int(ks[0]), list(K_range), wcss
    d = []
    for k, yi in zip(ks, y):
        w = np.array([k, yi]) - p1
        cross2d = v[0] * w[1] - v[1] * w[0]
        d.append(abs(cross2d) / n)
    return int(ks[int(np.argmax(d))]), list(K_range), wcss


def cluster_zones(vari_normalized, k):
    pixel_values = vari_normalized.reshape((-1, 1)).astype(np.float32)
    if float(np.std(pixel_values)) == 0.0:
        pixel_values = pixel_values + np.random.normal(0, 1e-6, pixel_values.shape).astype(np.float32)
    kmeans = KMeans(n_clusters=k, init='k-means++', random_state=42, n_init=10)
    labels = kmeans.fit_predict(pixel_values)
    return labels.reshape(vari_normalized.shape), kmeans.cluster_centers_.flatten()


def generate_zone_map(img_rgb, labels, k):
    h, w = labels.shape[:2]
    palette = np.array([
        [31, 119, 180], [255, 127, 14], [44, 160, 44], [214, 39, 40],
        [148, 103, 189], [140, 86, 75], [231, 119, 190], [127, 127, 127],
        [188, 189, 34], [23, 190, 207]
    ], dtype=np.uint8)
    out = palette[labels % len(palette)]
    overlay = cv2.addWeighted(img_rgb, 0.35, out, 0.65, 0)
    return overlay


def zone_recommendations(labels, centers, k, rain_mm):
    soils = ['Sandy', 'Loamy', 'Clayey']
    fc = {'Sandy': 0.20, 'Loamy': 0.30, 'Clayey': 0.40}
    root_depth_mm = 200.0
    rate_mm_per_hour = 15.0
    recs = []
    order = np.argsort(centers)
    for rank, z in enumerate(order):
        soil = soils[rank % len(soils)]
        moisture = float(np.clip(0.12 + 0.05 * rank, 0.10, 0.45))
        target = fc[soil] - 0.05
        deficit = max(0.0, target - moisture)
        rain_share = rain_mm * 0.5 / max(k, 1)
        water_mm = max(0.0, deficit * root_depth_mm - rain_share)
        minutes = max(0.0, water_mm / rate_mm_per_hour * 60.0)
        mask = labels == z
        ys, xs = np.where(mask)
        area_pct = 100.0 * mask.sum() / labels.size
        recs.append({
            'zone': int(rank + 1),
            'soil': soil,
            'moisture': round(moisture, 3),
            'deficit_mm': round(deficit * root_depth_mm, 1),
            'water_mm': round(water_mm, 1),
            'minutes': round(minutes, 1),
            'area_pct': round(area_pct, 1),
        })
    return recs


def fetch_rain(lat, lon, days=7):
    forecast = fetch_forecast(lat, lon, days)
    return forecast['total_mm']


def fetch_forecast(lat, lon, days=7):
    """Return {'total_mm': float, 'days': [{'date','mm'}, ...]} for the window."""
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           f"&daily=precipitation_sum&timezone=auto&forecast_days={days}")
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        daily = data.get("daily", {})
        dates = daily.get("time", []) or []
        amounts = daily.get("precipitation_sum", []) or []
        days_out = []
        for d, a in zip(dates, amounts):
            mm = 0.0 if a is None else float(a)
            days_out.append({"date": d, "mm": round(mm, 1)})
        total = round(sum(x["mm"] for x in days_out), 1)
        return {"total_mm": total, "days": days_out}
    except Exception:
        return {"total_mm": 0.0, "days": []}


def silhouette_for(vari, labels, k, max_samples=5000, seed=42):
    """Sampled silhouette score of the clustering (None when not computable)."""
    try:
        from sklearn.metrics import silhouette_score
        flat = vari.reshape(-1)
        n = flat.size
        if n > max_samples:
            rng = np.random.default_rng(seed)
            idx = rng.choice(n, size=max_samples, replace=False)
        else:
            idx = np.arange(n)
        X = flat[idx].reshape(-1, 1)
        y = labels.reshape(-1)[idx]
        if len(np.unique(y)) < 2 or len(y) <= len(np.unique(y)):
            return None
        return round(float(silhouette_score(X, y)), 4)
    except Exception:
        return None


ESRI_EXPORT = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
               "World_Imagery/MapServer/export")


def fetch_satellite_image(bbox, max_dim=1400):
    west, south, east, north = bbox
    if east <= west or north <= south:
        raise ValueError("Invalid bounding box.")

    span_x = east - west
    span_y = north - south
    aspect = span_x / span_y
    if aspect >= 1:
        w_px = max_dim
        h_px = max(200, int(round(max_dim / aspect)))
    else:
        h_px = max_dim
        w_px = max(200, int(round(max_dim * aspect)))

    url = (f"{ESRI_EXPORT}?bbox={west},{south},{east},{north}"
           f"&bboxSR=4326&imageSR=4326&size={w_px},{h_px}"
           f"&format=jpg&f=image")
    req = urllib.request.Request(url, headers={"User-Agent": "SmartEcoVRI/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("Satellite imagery service returned no image.")
    return img


def process_image(image_bgr, lat=26.9157, lon=70.9083, force_k=None):
    height, width = image_bgr.shape[:2]
    if width > 800:
        scale = 800 / width
        image_bgr = cv2.resize(image_bgr, (800, int(height * scale)))

    img_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    vari = compute_vari(img_rgb)

    sample = vari.reshape(-1, 1)[::10].astype(np.float32)
    if float(np.std(sample)) == 0.0:
        sample = sample + np.random.normal(0, 1e-6, sample.shape).astype(np.float32)
    auto_k, k_range, wcss = elbow_optimal_k(sample)

    k = int(force_k) if force_k else max(2, min(auto_k, 6))
    labels, centers = cluster_zones(vari, k)

    rain = fetch_rain(lat, lon)
    recs = zone_recommendations(labels, centers, k, rain)
    zone_map = generate_zone_map(img_rgb, labels, k)
    sil = silhouette_for(vari, labels, k)

    return {
        'k': k,
        'auto_k': auto_k,
        'k_range': k_range,
        'wcss': [round(float(x), 2) for x in wcss],
        'rain_mm': rain,
        'silhouette': sil,
        'recommendations': recs,
        'zone_map_bgr': cv2.cvtColor(zone_map, cv2.COLOR_RGB2BGR),
    }
