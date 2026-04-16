from pydantic import BaseModel, Field
from typing import List, Optional, Any
import logging

logger = logging.getLogger(__name__)

class VectorRequest(BaseModel):
    region_name: str
    geometry: Optional[Any] = None
    bbox: Optional[List[float]] = None
    data_types: List[str]
    run_id: str

class VectorTool:
    name = "fetch_vector"
    description = "Downloads OpenStreetMap boundaries or features (buildings, roads) for a region or bounding box."
    schema = VectorRequest

    async def execute(self, region_name: str, data_types: List[str], run_id: str, geometry: Optional[Any] = None, bbox: Optional[List[float]] = None) -> dict:
        import os
        from pathlib import Path
        import geopandas as gpd
        from shapely.geometry import shape
        
        runs_dir = Path("runs") / run_id / "outputs" / "vector"
        runs_dir.mkdir(parents=True, exist_ok=True)
        
        results = {}
        
        try:
            import osmnx as ox
            import shutil
            import geopandas as gpd
            from shapely.geometry import shape, mapping
            import asyncio
            
            # Temporarily set osmnx settings
            ox.settings.use_cache = True
            ox.settings.log_console = False
            
            loop = asyncio.get_running_loop()
            
            async def _export_gdf(gdf_to_export, type_name):
                # 1. GeoJSON
                output_geojson = runs_dir / f"{region_name.replace(' ', '_')}_{type_name}.geojson"
                await loop.run_in_executor(None, gdf_to_export.to_file, str(output_geojson), "GeoJSON")
                results[f"vector_{type_name}_geojson"] = str(output_geojson)
                
                # 2. Shapefile preparation (stringifying object columns)
                shp_gdf = gdf_to_export.copy()
                for col in shp_gdf.columns:
                    if shp_gdf[col].dtype == object and col != "geometry":
                        shp_gdf[col] = shp_gdf[col].astype(str)
                
                shp_dir = runs_dir / f"{region_name.replace(' ', '_')}_{type_name}_shp"
                shp_dir.mkdir(exist_ok=True)
                shp_file = shp_dir / f"{region_name.replace(' ', '_')}_{type_name}.shp"
                
                # Use a specific lambda for keyword arguments to run_in_executor if needed, but positional is fine
                def _write_shp():
                    shp_gdf.to_file(str(shp_file))
                await loop.run_in_executor(None, _write_shp)
                
                # 3. Zip it
                zip_path = runs_dir / f"{region_name.replace(' ', '_')}_{type_name}_shp.zip"
                def _write_zip():
                    shutil.make_archive(str(zip_path).replace('.zip', ''), 'zip', str(shp_dir))
                await loop.run_in_executor(None, _write_zip)
                
                results[f"vector_{type_name}_zip"] = str(zip_path)

            # If user wants boundary
            if "boundary" in data_types:
                try:
                    # Try using name to get detailed relation from OSM
                    gdf = await loop.run_in_executor(None, ox.geocode_to_gdf, region_name)
                except Exception as e:
                    logger.warning(f"Failed geocoding to gdf for {region_name}: {e}")
                    if geometry:
                        logger.info("Falling back to provided geometry.")
                        gdf = gpd.GeoDataFrame(index=[0], crs="epsg:4326", geometry=[shape(geometry)])
                    else:
                        raise
                
                await _export_gdf(gdf, "boundary")
                
                results["geometry"] = mapping(gdf.unary_union)
                results["bbox"] = list(gdf.total_bounds)
                
            # If user wants features (buildings/roads/etc)
            if "features" in data_types:
                tags = {'building': True, 'highway': True, 'waterway': True, 'natural': True, 'amenity': True}
                
                if geometry:
                    try:
                        geom = shape(geometry)
                        # Ensure polygon
                        if geom.geom_type in ["Point", "LineString"]:
                            geom = geom.buffer(0.01) # Approx 1km buffer
                        features = await loop.run_in_executor(None, ox.features_from_polygon, geom, tags)
                    except Exception as e:
                        logger.warning(f"Failed to fetch features by geometry: {e}. Falling back to region_name.")
                        features = await loop.run_in_executor(None, ox.features_from_place, region_name, tags)
                else:
                    features = await loop.run_in_executor(None, ox.features_from_place, region_name, tags)
                    
                await _export_gdf(features, "features")
                
        except Exception as e:
            logger.error(f"VectorTool execution failed: {e}")
            raise Exception(f"Failed to fetch vector data: {str(e)}")

        return results
