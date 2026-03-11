import requests
from bs4 import BeautifulSoup
import json
import os

def update_camera_data():
    # Adjusted to lowercase to match your repository
    local_kml = "locations.kml" 
    output_json = "camera_data.json"
    
    # Check for both cases just to be safe
    if not os.path.exists(local_kml):
        if os.path.exists("Locations.kml"):
            local_kml = "Locations.kml"
        else:
            print(f"Error: No KML file found in the repository root.")
            return

    try:
        with open(local_kml, 'r') as f:
            soup = BeautifulSoup(f, 'xml')
        
        # Follow the NetworkLink to the actual Google content
        network_link = soup.find('href')
        if not network_link:
            print("No <href> found inside the KML file.")
            return

        remote_url = network_link.text.strip()
        print(f"Fetching actual data from: {remote_url}")
        
        response = requests.get(remote_url)
        response.raise_for_status()
        remote_soup = BeautifulSoup(response.content, 'xml')
        
        cameras = []
        for pm in remote_soup.find_all('Placemark'):
            name = pm.find('name').text.strip() if pm.find('name') else "Unknown"
            coords = pm.find('coordinates').text.strip() if pm.find('coordinates') else ""
            
            if coords:
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
