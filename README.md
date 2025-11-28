# 🌱 Smart Eco-VRI: Low-Cost Variable Rate Irrigation System

![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python)
![Platform](https://img.shields.io/badge/Platform-ESP32-green?logo=espressif)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Status](https://img.shields.io/badge/Status-Prototype-orange)

> **A satellite-driven, IoT-enabled precision irrigation solution designed specifically for smallholder farmers (<5 acres).**

---

## 📖 Overview

**Smart Eco-VRI** is a "Brownfield" retrofit system that upgrades traditional irrigation setups into intelligent, data-driven networks for under **$90 USD**. 

Instead of using expensive drones or dense sensor grids, this project utilizes **Free Satellite/Aerial Imagery** and **Unsupervised Machine Learning (K-Means Clustering)** to scientifically identify the minimum number of irrigation zones required for a field. An **ESP32 microcontroller** then manages these zones dynamically based on real-time soil moisture, crop root growth models, and weather forecasts.

### 🎯 Key Features
* **Satellite Zoning:** Uses the Elbow Method (K-Means) on RGB/NDVI imagery to find optimal sensor placement.
* **Dynamic "Soil Tank" Model:** Calculates water deficits based on daily root depth growth, not just static thresholds.
* **Weather Aware:** Integrates OpenWeatherMap API to auto-cancel irrigation if rain is forecast (Predictive Conservation).
* **Cost-Effective:** Drastically reduces hardware costs by optimizing the number of valves and sensors needed.

---

## ⚙️ System Architecture

The system operates in two distinct phases:

1.  **Phase 1: Zoning (The "Eye")** - Python script processes aerial images to generate a zoning map.
2.  **Phase 2: Control (The "Brain")** - ESP32 runs the daily water balance algorithm.

![System Architecture](images/system_architecture.png)
*(Place your architecture diagram here)*

---

## 🛠️ Tech Stack & Hardware

### Software
* **Python:** OpenCV, Scikit-Learn, Matplotlib (for Image Processing & Clustering).
* **C++ / Arduino IDE:** Firmware for ESP32.
* **APIs:** OpenWeatherMap (Forecast Data).

### Hardware Bill of Materials (BOM)
| Component | Function | Approx Cost |
| :--- | :--- | :--- |
| **ESP32 DevKit V1** | Main Controller (Wi-Fi/Dual Core) | $6.00 |
| **Capacitive Soil Sensors** | Corrosion-resistant moisture reading | $2.00 (per unit) |
| **4-Channel Relay Module** | Controls high-power valves | $4.00 |
| **12V Solenoid Valves** | Actuators for water flow | $10.00 (per unit) |
| **Power Supply** | 12V DC Adapter | $10.00 |

---

## 🚀 Getting Started

### Prerequisites
1.  Python 3.x installed.
2.  Arduino IDE setup for ESP32.
3.  An OpenWeatherMap API Key.

### Step 1: Run the Zoning Algorithm
This step determines how many zones/valves you need.

1.  Navigate to the `zoning_script/` folder.
2.  Place your farm image (screenshot from Google Earth) in the folder.
3.  Install dependencies:
    ```bash
    pip install opencv-python numpy matplotlib scikit-learn
    ```
4.  Run the optimizer:
    ```bash
    python Zone_Optimizer_RGB.py
    ```
5.  **Output:** The script will generate an "Elbow Graph". Pick the `k` value where the curve bends (e.g., k=3).

![Elbow Graph](images/elbow_real_example.png)
*(Example of the Elbow Method output)*

### Step 2: Flash the ESP32
1.  Open `firmware/Smart_Irrigation_Controller.ino` in Arduino IDE.
2.  Install required libraries: `WiFi`, `ArduinoJson`, `Preferences`.
3.  Update the **Configuration Section** with your WiFi credentials, API Key, and the calibrated FC/PWP values from your sensors.
4.  Upload to ESP32.

---

## 📊 How It Works (The Logic)

The ESP32 runs a **Non-Blocking State Machine** that wakes up hourly to perform the following calculation:

1.  **Calculate Root Depth ($Z_r$):** $$Z_{current} = Z_{min} + \left( \frac{Day_{current}}{Days_{mature}} \right) \times (Z_{max} - Z_{min})$$
    *(Prevents over-watering young plants)*

2.  **Calculate Deficit ($D$):** $$D = (FieldCapacity - CurrentMoisture) \times Z_{current}$$

3.  **Net Requirement ($NIR$):**
    $$NIR = D - ForecastedRain$$
    *(If Rain > Deficit, the system SKIPS irrigation to save water)*

---

## 📂 Repository Structure
