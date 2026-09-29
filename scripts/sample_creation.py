import cv2
import numpy as np

# 1. Create a blank white canvas (500x500 pixels)
height, width = 500, 500
image = np.ones((height, width, 3), dtype=np.uint8) * 255 

# 2. Draw Zone 1: Healthy Vegetation (Dark Green)
# Rectangle at top-left
cv2.rectangle(image, (50, 50), (250, 450), (34, 139, 34), -1) 

# 3. Draw Zone 2: Stressed Vegetation (Yellow-Green)
# Rectangle at top-right
cv2.rectangle(image, (250, 50), (450, 250), (144, 238, 144), -1)

# 4. Draw Zone 3: Bare Soil (Brown)
# Rectangle at bottom-right
cv2.rectangle(image, (250, 250), (450, 450), (92, 64, 51), -1)

# 5. Add some "Noise" (Random speckles to make it realistic)
noise = np.random.normal(0, 15, image.shape).astype(np.uint8)
image = cv2.add(image, noise)

# 6. Save the file
filename = "Synthetic_Farm_Test.jpg"
cv2.imwrite(filename, image)
print(f"Success! Saved '{filename}'. Use this to test your Zone Optimizer.")