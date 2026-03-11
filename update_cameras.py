import requests
from bs4 import BeautifulSoup
import json
import os

def update_camera_data():
    local_kml = "Locations.kml"
    output_json = "camera_data.json"
    
    if not os.path.exists(local_kml):
        print(f"Error: {local_kml} not found in the repository root.")
        return

    try:
        # Read the pointer KML you uploaded
        with open(local_kml, 'r') as f:
            soup = BeautifulSoup(f, 'xml')
        
        # Follow the NetworkLink to the actual Google content
        remote_url = soup.find('href').text.strip()
        print(f"Fetching real data from: {remote_url}")
        
        response = requests.get(remote_url)
        response.raise_for_status()
        remote_soup = BeautifulSoup(response.content, 'xml')
        
        cameras = []
        # Parse actual Placemarks from the remote Google data
        for pm in remote_soup.find_all('Placemark'):
            name = pm.find('name').text.strip() if pm.find('name') else "Unknown"
            coords = pm.find('coordinates').text.strip() if pm.find('coordinates') else ""
            
            if coords:
                # KML format: longitude, latitude, altitude
                parts = coords.split(',')
                if len(parts) >= 2:
                    cameras.append({
                        "name": name,
                        "longitude": parts[0],
                        "latitude": parts[1]
                    })

        with open(output_json, 'w') as f:
            json.dump(cameras, f, indent=4)
            
        print(f"Success! {len(cameras)} cameras saved to {output_json}.")
        
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    update_camera_data()
