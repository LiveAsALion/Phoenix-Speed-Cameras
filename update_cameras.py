import requests
from bs4 import BeautifulSoup
import json
import re

def extract_direction(text):
    """Maps text descriptions to degree headings (0, 90, 180, 270)."""
    mapping = {
        "N/B": 0, "NB": 0, "NORTHBOUND": 0,
        "S/B": 180, "SB": 180, "SOUTHBOUND": 180,
        "E/B": 90, "EB": 90, "EASTBOUND": 90,
        "W/B": 270, "WB": 270, "WESTBOUND": 270
    }
    text_upper = text.upper()
    for key, degree in mapping.items():
        if re.search(rf'\b{key}\b', text_upper):
            return degree
    return None

def update_camera_data():
    # Primary KML pointer
    KML_URL = "https://www.google.com/maps/d/kml?forcekml=1&mid=1aB99-IfJH8EKHO_nVtF-xhgsMTKU_mw&lid=zEYtk9GgoDg"
    output_json = "camera_data.json"
    
    try:
        # Step 1: Fetch the initial KML
        response = requests.get(KML_URL)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'xml')
        
        # Step 2: Extract the actual data from the Network Link
        # If the URL is just a pointer, this finds the real source
        network_link = soup.find('href')
        if network_link:
            actual_url = network_link.text.strip()
            print(f"Following Network Link to: {actual_url}")
            response = requests.get(actual_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'xml')

        cameras = []
        # Step 3: Parse Placemarks
        for pm in soup.find_all('Placemark'):
            name = pm.find('name').text.strip() if pm.find('name') else ""
            desc = pm.find('description').text.strip() if pm.find('description') else ""
            coords = pm.find('coordinates').text.strip() if pm.find('coordinates') else ""
            
            # Combine name and description to search for directional tags
            direction = extract_direction(f"{name} {desc}")
            
            if coords and direction is not None:
                # KML coords are: longitude, latitude, altitude
                parts = coords.split(',')
                if len(parts) >= 2:
                    cameras.append({
                        "name": name,
                        "latitude": float(parts[1]),
                        "longitude": float(parts[0]),
                        "direction_deg": direction
                    })

        # Step 4: Save the final JSON
        if cameras:
            with open(output_json, 'w') as f:
                json.dump(cameras, f, indent=4)
            print(f"Success! {len(cameras)} cameras processed with directional data.")
        else:
            print("No valid camera locations found.")

    except Exception as e:
        print(f"Error during execution: {e}")

if __name__ == "__main__":
    update_camera_data()
