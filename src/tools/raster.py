import logging
import asyncio
from typing import List, Optional, Any
from pathlib import Path

from src.config import settings

logger = logging.getLogger(__name__)

async def fetch_raster_data(
    region_name: str, start_date: str, end_date: str, data_types: List[str], run_id: str, 
    cloud_cover_lt: int = 20, max_items: int = 1, bbox: Optional[List[float]] = None, geometry: Optional[Any] = None
) -> dict:
    import planetary_computer as pc
    from pystac_client import Client
    import odc.stac
    
    runs_dir = Path(settings.RUNS_DIR) / run_id / "outputs" / "raster"
    runs_dir.mkdir(parents=True, exist_ok=True)
    
    if not bbox and not geometry:
        raise ValueError("Spatial context must be established first (bbox or geometry required).")
        
    results = {}
    loop = asyncio.get_running_loop()
    
    def _sync_fetch():
        client = Client.open(
            "https://planetarycomputer.microsoft.com/api/stac/v1",
            modifier=pc.sign_inplace
        )
        
        search_kwargs = {
            "collections": ["sentinel-2-l2a"],
            "datetime": f"{start_date}/{end_date}",
            "query": {"eo:cloud_cover": {"lt": cloud_cover_lt}},
            "max_items": max_items,
            "sortby": [{"field": "eo:cloud_cover", "direction": "asc"}]
        }
        
        if bbox:
            search_kwargs["bbox"] = bbox
        elif geometry:
            search_kwargs["intersects"] = geometry
            
        search = client.search(**search_kwargs)
        items = list(search.items())
        
        if not items:
            raise Exception(f"No satellite imagery found between {start_date} and {end_date}.")
            
        item = items[0]
        
        assets_to_fetch = set()
        if any(dt in data_types for dt in ["true_color", "aerial"]):
            assets_to_fetch.add("visual")
        if any(dt in data_types for dt in ["red", "ndvi"]):
            assets_to_fetch.add("B04")
        if any(dt in data_types for dt in ["nir", "ndvi"]):
            assets_to_fetch.add("B08")
            
        ds = odc.stac.load(
            [item],
            bands=list(assets_to_fetch),
            bbox=bbox,
            geopolygon=geometry,
            resolution=10, 
        ).squeeze("time")
        
        paths = {}
        if "visual" in assets_to_fetch:
            path = runs_dir / f"{region_name.replace(' ', '_')}_true_color.tif"
            import rioxarray
            if "visual" in ds.data_vars:
                ds["visual"].rio.to_raster(str(path))
                paths["raster_true_color"] = str(path)
        
        if "ndvi" in data_types:
            red = ds["B04"].astype(float)
            nir = ds["B08"].astype(float)
            ndvi = (nir - red) / (nir + red)
            ndvi_path = runs_dir / f"{region_name.replace(' ', '_')}_ndvi.tif"
            ndvi.rio.to_raster(str(ndvi_path))
            paths["raster_ndvi"] = str(ndvi_path)
            
        return paths

    try:
        output_paths = await loop.run_in_executor(None, _sync_fetch)
        results.update(output_paths)
    except Exception as e:
        logger.error(f"Raster fetch failed: {e}")
        raise Exception(f"Failed to fetch satellite data: {str(e)}")

    return results
