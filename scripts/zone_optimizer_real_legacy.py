import cv2
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from sklearn.cluster import KMeans
import urllib.request
import json
import os

# ==========================================
# CONFIGURATION
# ==========================================
image_path = r"jais_farm.jpg"  # Ensure this file exists
lat = 26.9157
lon = 70.9083
force_k = 3  # Set to None to use interactive picking

# ==========================================
# STEP 1: LOAD AND PREPROCESS IMAGE
# ==========================================
print(f"Loading image: {image_path}...")
if not os.path.exists(image_path):
    # Create a synthetic image if file is missing (For testing)
    print("Image not found. Generating synthetic test data...")
    h, w = 500, 500
    img = np.zeros((h, w), dtype=np.uint8)
    cv2.circle(img, (150, 150), 100, 200, -1) # Zone 1
    cv2.rectangle(img, (300, 0), (500, 500), 100, -1) # Zone 2
    # Add noise
    noise = np.random.normal(0, 10, img.shape).astype(np.uint8)
    img = cv2.add(img, noise)
else:
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)

# Resize for speed optimization during clustering
height, width = img.shape
if width > 800:
    scale_percent = 800 / width
    width = int(width * scale_percent)
    height = int(height * scale_percent)
    img = cv2.resize(img, (width, height))

# Flatten image for K-Means
pixel_values = img.reshape((-1, 1))
pixel_values = np.float32(pixel_values)

# ==========================================
# STEP 2: ELBOW METHOD ANALYSIS
# ==========================================
print("Running Elbow Analysis...")
wcss = []
K_range = range(1, 10)

for k in K_range:
    kmeans = KMeans(n_clusters=k, init='k-means++', random_state=42, n_init=10)
    kmeans.fit(pixel_values)
    wcss.append(kmeans.inertia_)

# Plot and Save Elbow Graph for the Paper
plt.figure(figsize=(8, 5))
plt.plot(K_range, wcss, 'bo-', linewidth=2, markersize=8)
plt.title('Elbow Method for Optimal Zone Detection', fontsize=14)
plt.xlabel('Number of Clusters (k)', fontsize=12)
plt.ylabel('Within-Cluster Sum of Squares (WCSS)', fontsize=12)
plt.grid(True, linestyle='--', alpha=0.7)
plt.savefig('elbow_real.pdf', dpi=300) # Save as PDF for LaTeX
plt.close()

# ==========================================
# STEP 3: CLUSTERING & ZONING
# ==========================================
optimal_k = force_k if force_k else 3
print(f"Applying K-Means with k={optimal_k}...")

kmeans = KMeans(n_clusters=optimal_k, random_state=42, n_init=10)
labels = kmeans.fit_predict(pixel_values)
clustered_img = labels.reshape(img.shape)

# ==========================================
# STEP 4: WEATHER INTEGRATION & RECOMMENDATION
# ==========================================
def get_rain_forecast(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=precipitation_sum&timezone=auto"
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read().decode())
            # Sum next 2 days of rain
            rain_mm = sum(data['daily']['precipitation_sum'][:2])
            return rain_mm
    except:
        return 0.0

rain_forecast = get_rain_forecast(lat, lon)
print(f"Rain Forecast (48h): {rain_forecast} mm")

# Generate Recommendation Overlay
plt.figure(figsize=(10, 8))
cmap = ListedColormap(['#FFD700', '#228B22', '#8B4513']) # Gold, ForestGreen, SaddleBrown
plt.imshow(clustered_img, cmap=cmap)
plt.title(f'Smart Eco-VRI Zone Map (k={optimal_k})\nRain Forecast: {rain_forecast}mm', fontsize=16)
plt.axis('off')

# Annotate Zones
unique_labels = np.unique(labels)
for label in unique_labels:
    mask = (clustered_img == label)
    # Find center of mass for the label
    y_coords, x_coords = np.where(mask)
    cy, cx = int(np.mean(y_coords)), int(np.mean(x_coords))
    
    # Simple logic for demo: Darker pixels (lower value) = Wet/Clay, Lighter = Dry/Sand
    # In real deployment, you'd map this to the specific cluster center value
    avg_intensity = np.mean(img[mask])
    
    if avg_intensity < 80:
        soil_type = "Clay/Wet"
        irri_time = 0 if rain_forecast > 5 else 15
    elif avg_intensity < 160:
        soil_type = "Loam"
        irri_time = 0 if rain_forecast > 10 else 30
    else:
        soil_type = "Sand/Dry"
        irri_time = 0 if rain_forecast > 15 else 45
        
    text = f"ZONE {label+1}\n{soil_type}\nRun: {irri_time}m"
    plt.text(cx, cy, text, color='white', fontsize=10, fontweight='bold', 
             ha='center', va='center', bbox=dict(facecolor='black', alpha=0.6, edgecolor='none'))

plt.tight_layout()
plt.savefig('zones_output_real.png', dpi=300)
print("Outputs generated: elbow_real.pdf, zones_output_real.png")
plt.show()