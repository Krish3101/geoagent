import httpx
from urllib.parse import quote

async def geocode_location(location: str) -> dict:
    headers = {"User-Agent": "GeoAgent_v4"}
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
                bbox_str = place["boundingbox"]
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
            words.pop(0)

        raise ValueError(f"Could not geocode location: {location}")
