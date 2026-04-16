from pydantic import BaseModel
from typing import Optional, List, Any

class GeocodeRequest(BaseModel):
    location: str

class GeocodeTool:
    name = "geocode"
    description = "Given a location name (e.g. 'Paris, France' or 'Yellowstone'), returns its bounding box and geometry."
    schema = GeocodeRequest

    async def execute(self, location: str) -> dict:
        import httpx
        from urllib.parse import quote
        
        headers = {"User-Agent": "GeoAgent_v4"}
        
        # Split location to allow progressive fallback (e.g., "MIT ADT Loni Kalbhor" -> "Loni Kalbhor")
        words = location.strip().split()
        
        async with httpx.AsyncClient() as client:
            while words:
                current_query = " ".join(words)
                url = f"https://nominatim.openstreetmap.org/search?q={quote(current_query)}&format=json&polygon_geojson=1&limit=1"
                
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                
                if data:
                    place = data[0]
                    bbox_str = place["boundingbox"] # [lat_min, lat_max, lon_min, lon_max]
                    bbox = [
                        float(bbox_str[2]), # minx
                        float(bbox_str[0]), # miny
                        float(bbox_str[3]), # maxx
                        float(bbox_str[1])  # maxy
                    ]
                    
                    return {
                        "bbox": bbox,
                        "geometry": place.get("geojson"),
                        "resolved_location": current_query
                    }
                
                # If no data, drop the first word and try again
                words.pop(0)

            raise ValueError(f"Could not geocode location: {location}")
