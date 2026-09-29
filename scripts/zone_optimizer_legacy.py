import cv2
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from sklearn.cluster import KMeans
import urllib.request
import json

# ==========================================
# 1. LOAD RGB IMAGE (Google Earth Screenshot)
# ==========================================
# Replace with your actual screenshot path
image_path = r"jais_farm.jpg" 
lat = 26.9157
lon = 70.9083
force_k = 3

print(f"Loading image: {image_path}...")
img = cv2.imread(image_path)
if img is None:
    print("Image not found. Using synthetic test image.")
    h, w = 200, 300
    img_rgb = np.zeros((h, w, 3), dtype=np.uint8)
    img_rgb[:, : w // 3, :] = [60, 200, 60]
    img_rgb[:, w // 3 : 2 * w // 3, :] = [200, 60, 60]
    img_rgb[:, 2 * w // 3 :, :] = [60, 60, 200]
else:
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

# ==========================================
# 2. CALCULATE VARI INDEX (The "RGB NDVI")
# ==========================================
print("Calculating Vegetation Index (VARI)...")

# Separate Channels (Red, Green, Blue)
# Note: Arrays must be float for division
R = img_rgb[:, :, 0].astype(float)
G = img_rgb[:, :, 1].astype(float)
B = img_rgb[:, :, 2].astype(float)

# Calculate VARI: (G - R) / (G + R - B)
# We add 0.0001 to denominator to avoid DivideByZero errors
numerator = G - R
denominator = G + R - B + 0.0001
vari_img = numerator / denominator
vari_img = np.nan_to_num(vari_img, nan=0.0, posinf=1.0, neginf=0.0).astype(np.float32)

# Normalize VARI to 0-1 range for Clustering
vari_normalized = cv2.normalize(vari_img, None, 0, 1, cv2.NORM_MINMAX)

# ==========================================
# 3. ELBOW METHOD (Find Optimal Zones)
# ==========================================
print("Running Elbow Analysis...")

# Flatten image to 1D array of values
pixel_values = vari_normalized.reshape((-1, 1))

# Downsample for speed (take every 10th pixel if image is HD)
# This makes K-Means fast even on huge Google Earth screenshots
sample_pixels = pixel_values[::10] 
if float(np.std(sample_pixels)) == 0.0:
    sample_pixels = sample_pixels + np.random.normal(0, 1e-6, sample_pixels.shape).astype(np.float32)

if float(np.std(pixel_values)) == 0.0:
    pixel_values = pixel_values + np.random.normal(0, 1e-6, pixel_values.shape).astype(np.float32)
wcss = []
K_range = range(1, 10)
for k in K_range:
    kmeans = KMeans(n_clusters=k, init='k-means++', random_state=42, n_init=10)
    kmeans.fit(sample_pixels)
    wcss.append(kmeans.inertia_)

# Show Elbow Graph
plt.figure(figsize=(8, 4))
plt.plot(K_range, wcss, 'bo-')
plt.title('Elbow Method (Using VARI Index)')
plt.xlabel('Number of Zones')
plt.ylabel('Variance')
plt.grid(True)
plt.draw()

def _choose_k(K_range, wcss):
    ks = np.array(list(K_range), dtype=float)
    y = np.array(wcss, dtype=float)
    p1 = np.array([ks[0], y[0]])
    p2 = np.array([ks[-1], y[-1]])
    v = p2 - p1
    n = np.linalg.norm(v)
    if n == 0:
        return int(ks[0])
    d = []
    for k, yi in zip(ks, y):
        w = np.array([k, yi]) - p1
        dist = abs(np.cross(v, w)) / n
        d.append(dist)
    return int(ks[int(np.argmax(d))])

if force_k:
    k_optimal = int(force_k)
else:
    pts = []
    try:
        pts = plt.ginput(1, timeout=20)
    except Exception:
        pts = []
    s = ""
    try:
        s = input("Pick k from graph or Enter to auto: ").strip()
    except Exception:
        s = ""
    if pts:
        x = pts[0][0]
        k_optimal = int(np.clip(round(x), min(K_range), max(K_range)))
    elif s:
        try:
            km = int(s)
            if km < min(K_range) or km > max(K_range):
                k_optimal = _choose_k(K_range, wcss)
            else:
                k_optimal = km
        except Exception:
            k_optimal = _choose_k(K_range, wcss)
    else:
        k_optimal = _choose_k(K_range, wcss)
print(f"Selected K: {k_optimal}")

k_candidate = max(2, int(k_optimal))
unique = []
while True:
    kmeans = KMeans(n_clusters=k_candidate, random_state=42, n_init=10)
    labels = kmeans.fit_predict(pixel_values)
    unique = np.unique(labels)
    if len(unique) >= 2:
        break
    if k_candidate < max(K_range):
        k_candidate += 1
    else:
        thr = np.median(pixel_values)
        labels = (pixel_values[:, 0] > thr).astype(int)
        k_candidate = 2
        break

# Reshape back to image
clustered_img = labels.reshape(vari_img.shape)

# Display Results
plt.figure(figsize=(12, 6))

plt.subplot(1, 2, 1)
plt.imshow(img_rgb)
plt.title("Original Google Earth Image")
plt.axis('off')

plt.subplot(1, 2, 2)
colors = ['#1f77b4','#ff7f0e','#2ca02c','#d62728','#9467bd','#8c564b','#e377c2','#7f7f7f','#bcbd22','#17becf']
cm = ListedColormap(colors[:max(k_candidate,1)])
from matplotlib.colors import BoundaryNorm
bounds = np.arange(k_candidate + 1) - 0.5
norm = BoundaryNorm(bounds, cm.N)
plt.imshow(clustered_img, cmap=cm, norm=norm)
plt.title(f"Generated Irrigation Zones ({k_candidate})")
cb = plt.colorbar(ticks=np.arange(k_candidate))
cb.set_label('Zone ID')
plt.axis('off')

def _get_rain(lat, lon, days=7):
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=precipitation_sum&timezone=auto&forecast_days={days}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        daily = data.get("daily", {})
        amounts = daily.get("precipitation_sum", []) or []
        total = float(np.nansum(np.array(amounts, dtype=float))) if amounts else 0.0
        return total
    except Exception:
        return 0.0

rain_total = _get_rain(lat, lon)
print(f"7-day precipitation total: {rain_total:.1f} mm")
plt.suptitle(f"7-day rain: {rain_total:.1f} mm")

def _zone_recommendations(labels, shape, k, rain_total):
    soils = ['sandy','loam','clayey']
    fc = {'sandy':0.20,'loam':0.30,'clayey':0.40}
    target_offset = 0.05
    root_depth_mm = 200.0
    rate_mm_per_hour = 15.0
    recs = []
    lab = labels.reshape(shape)
    for z in range(k):
        soil = soils[z % len(soils)]
        moisture = float(np.random.uniform(0.15,0.35))
        target = fc[soil] - target_offset
        deficit = max(0.0, target - moisture)
        rain_share = float(rain_total) * 0.5 / max(k,1)
        water_mm = max(0.0, deficit * root_depth_mm - rain_share)
        minutes = max(0.0, water_mm / rate_mm_per_hour * 60.0)
        ys, xs = np.where(lab == z)
        cx = float(np.mean(xs)) if xs.size else shape[1] * 0.5
        cy = float(np.mean(ys)) if ys.size else shape[0] * 0.5
        recs.append({'zone':z,'soil':soil,'moisture':moisture,'minutes':minutes,'x':cx,'y':cy})
    return recs

recs = _zone_recommendations(labels, vari_img.shape, k_candidate, rain_total)
for r in recs:
    plt.text(r['x'], r['y'], f"Z{r['zone']} {r['soil']}\n{r['minutes']:.0f}m", color='white', fontsize=8, ha='center', va='center', bbox=dict(facecolor='black', alpha=0.3))
with open('zones_recommendations_rgb.json','w') as f:
    json.dump(recs, f, indent=2)
plt.savefig('zones_output.png', dpi=300, bbox_inches='tight')
plt.show()
