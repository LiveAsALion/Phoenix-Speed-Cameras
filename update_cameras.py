import requests
import re
import json
import os

# 1. Configuration
# Replace this with your actual public Google My Maps URL
MAPS_URL = "https://www.google.com/maps/d/embed?mid=1aB99-IfJH8EKHO_nVtF-xhgsMTKU_mw&ehbc=2E312F"

def get_direction_deg(name):
    """
    Translates compass shorthand into degrees for Tasker math.
    EB = 90, WB = 270, NB = 0, SB = 180
    """
    name = name.upper()
    if any(x in name for x in ['EB', 'EAST']): return 90
    if any(x in name for x in ['WB', 'WEST']): return 270
    if any(x in name for x in ['NB', 'NORTH']): return 0
    if any(x in name for x in ['SB', 'SOUTH']): return 180
    return 0  # Default fallback

def update_camera_data():
    print("Fetching data from Google Maps...")
    try:
        response = requests.get(MAPS_URL, timeout=15)
        response.raise_for_status()
        content = response.text
    except Exception as e:
        print(f"Failed to fetch map: {e}")
        return

    # 2. Extract Data using Regex
    # This pattern captures: [longitude, latitude, 0], "Camera Name"
    # Note: Google stores it as [long, lat], but Tasker needs [lat, long]
    pattern = r'\[(-?\d+\.\d+),(-?\d+\.\d+),0\],\"(.*?)\"'
    matches = re.findall(pattern, content)

    camera_list = []
    
    for lon, lat, name in matches:
        # Clean up the name (Google sometimes escapes characters)
        clean_name = name.encode('utf-8').decode('unicode_escape').replace('\\', '')
        
        camera_list.append({
            "name": clean_name,
            "latitude": float(lat),
            "longitude": float(lon),
            "direction_deg": get_direction_deg(clean_name)
        })

    # 3. Validation & Saving
    if not camera_list:
        print("Error: No cameras found. The Regex pattern may need adjustment.")
        # Print a snippet of content for debugging in GitHub Logs
        print("Page Content Snippet:", content[:500])
        return

    # Remove duplicates if any
    unique_cameras = { (c['latitude'], c['longitude']): c for c in camera_list }.values()
    final_list = list(unique_cameras)

    with open('camera_data.json', 'w', encoding='utf-8') as f:
        json.dump(final_list, f, indent=4)
    
    print(f"Success! Found and saved {len(final_list)} camera locations.")

if __name__ == "__main__":
    update_camera_data()
