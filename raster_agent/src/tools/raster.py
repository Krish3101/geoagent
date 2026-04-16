from pydantic import BaseModel, Field
from typing import List, Optional, Any
import logging

logger = logging.getLogger(__name__)

class RasterRequest(BaseModel):
    region_name: str
    start_date: str
    end_date: str
    data_types: List[str]
    cloud_cover_lt: int = 20
    max_items: int = 1
    bbox: Optional[List[float]] = None
    geometry: Optional[Any] = None
    run_id: str

class RasterTool:
    name = "fetch_raster"
    description = "Downloads Sentinel-2 satellite imagery (true_color, ndvi, red, green, blue, nir, scl)."
    schema = RasterRequest

    async def execute(self, region_name: str, start_date: str, end_date: str, data_types: List[str], run_id: str, 
                      cloud_cover_lt: int = 20, max_items: int = 1, bbox: Optional[List[float]] = None, geometry: Optional[Any] = None) -> dict:
        import os
        from pathlib import Path
        import planetary_computer as pc
        from pystac_client import Client
        import odc.stac
        import asyncio
        import xarray as xr
        import numpy as np
        
        runs_dir = Path("runs") / run_id / "outputs" / "raster"
        runs_dir.mkdir(parents=True, exist_ok=True)
        
        if not bbox and not geometry:
            raise ValueError("RasterTool requires either bbox or geometry. Run geocode or vector first.")
            
        results = {}
        
        try:
            # Recreate planetary computer code securely in asyncio thread
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
                    raise Exception(f"No satellite imagery found for {region_name} between {start_date} and {end_date}.")
                    
                item = items[0]
                
                assets_to_fetch = set()
                if "true_color" in data_types:
                    assets_to_fetch.add("visual")
                if "aerial" in data_types:
                    assets_to_fetch.add("visual")
                if "red" in data_types or "ndvi" in data_types:
                    assets_to_fetch.add("B04")
                if "green" in data_types:
                    assets_to_fetch.add("B03")
                if "blue" in data_types:
                    assets_to_fetch.add("B02")
                if "nir" in data_types or "ndvi" in data_types:
                    assets_to_fetch.add("B08")
                if "scl" in data_types:
                    assets_to_fetch.add("SCL")
                    
                # Load lazily into xarray
                ds = odc.stac.load(
                    [item],
                    bands=list(assets_to_fetch),
                    bbox=bbox,
                    geopolygon=geometry,
                    resolution=10, 
                ).squeeze("time")
                
                paths = {}
                # Calculate True Color
                if "visual" in assets_to_fetch:
                    path = runs_dir / f"{region_name.replace(' ', '_')}_true_color.tif"
                    # Simple conversion script for true_color...
                    import rioxarray
                    # Assuming ds["visual"] exists
                    if "visual" in ds.data_vars:
                        # Convert tuple/data buffer to TIF
                        ds["visual"].rio.to_raster(str(path))
                        paths["raster_true_color"] = str(path)
                
                # Calculate NDVI
                if "ndvi" in data_types:
                    red = ds["B04"].astype(float)
                    nir = ds["B08"].astype(float)
                    ndvi = (nir - red) / (nir + red)
                    ndvi_path = runs_dir / f"{region_name.replace(' ', '_')}_ndvi.tif"
                    ndvi.rio.to_raster(str(ndvi_path))
                    paths["raster_ndvi"] = str(ndvi_path)
                    
                return paths

            # Execute synchronous code in executor
            output_paths = await loop.run_in_executor(None, _sync_fetch)
            results.update(output_paths)
            
        except ImportError as e:
            logger.error(f"Missing raster dependencies: {e}")
            raise Exception("Please install required raster dependencies: planetary-computer pystac-client odc-stac rioxarray")
        except Exception as e:
            logger.error(f"RasterTool execution failed: {e}")
            raise Exception(f"Failed to fetch satellite data: {str(e)}")

        return results
