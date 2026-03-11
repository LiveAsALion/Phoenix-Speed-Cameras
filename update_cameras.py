import requests
from bs4 import BeautifulSoup
import json

def update_camera_data():
    # We are now hardcoding the URL you provided directly into the script
    KML_URL = "https://www.google.com/maps/d/kml?forcekml=1&mid=1aB99-IfJH8EKHO_nVtF-xhgsMTKU_mw&lid=zEYtk9GgoDg"
    output_json = "camera_data.json"
    
    print(f"Fetching camera data directly from: {KML_URL}")

    try:
        response = requests.get(KML_URL)
        response.raise_for_status()
        
        # Parse the remote Google data
        soup = BeautifulSoup(response.content, 'xml')
        
        cameras = []
        # Find all camera locations in the Google Map data
        for pm in soup.find_all('Placemark'):
            name = pm.find('name').text.strip() if pm.find('name') else "Unknown"
            coords = pm.find('coordinates').text.strip() if pm.find('coordinates') else ""
            
            if coords:
                # KML coords are: longitude, latitude, altitude
                parts = coords.split(',')
                if len(parts) >= 2:
                    cameras.append({
                        "name": name,
                        "longitude": parts[0],
                        "latitude": parts[1]
                    })

        if not cameras:
            print("No cameras found. The URL might not be returning data correctly.")
            return

        with open(output_json, 'w') as f:
            json.dump(cameras, f, indent=4)
            
        print(f"Success! {len(cameras)} cameras saved to {output_json}.")
        
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    update_camera_data()
