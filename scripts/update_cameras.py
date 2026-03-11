import requests
from bs4 import BeautifulSoup
import json

def update_camera_data():
    local_kml = "Locations.kml"
    output_json = "camera_data.json"
    
    try:
        # 1. Read your local pointer file
        with open(local_kml, 'r') as f:
            local_soup = BeautifulSoup(f, 'xml')
        
        # 2. Find the remote data URL 
        network_link = local_soup.find('href')
        if not network_link:
            print("No NetworkLink found in Locations.kml")
            return
        
        remote_url = network_link.text.strip()
        print(f"Fetching actual data from: {remote_url}")
        
        # 3. Fetch the actual camera data from Google
        response = requests.get(remote_url)
        response.raise_for_status()
        remote_soup = BeautifulSoup(response.content, 'xml')
        
    except Exception as e:
        print(f"Error during KML processing: {e}")
        return

    # 4. Parse the actual locations
    camera_list = []
    placemarks = remote_soup.find_all('Placemark')
    
    for pm in placemarks:
        name = pm.find('name').text.strip() if pm.find('name') else "Unknown"
        coords = pm.find('coordinates').text.strip() if pm.find('coordinates') else ""
        
        if coords:
            # Coordinates are usually: longitude, latitude, altitude
            parts = coords.split(',')
            if len(parts) >= 2:
                camera_list.append({
                    "name": name,
                    "longitude": parts[0],
                    "latitude": parts[1]
                })

    # 5. Save the final data
    if camera_list:
        with open(output_json, 'w') as f:
            json.dump(camera_list, f, indent=4)
        print(f"Success! Saved {len(camera_list)} cameras to {output_json}")
    else:
        print("No camera coordinates found at the remote URL.")

if __name__ == "__main__":
    update_camera_data()
