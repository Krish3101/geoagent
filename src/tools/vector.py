import logging
import asyncio
import shutil
from typing import List, Optional, Any
from pathlib import Path

logger = logging.getLogger(__name__)

async def fetch_vector_data(
    region_name: str, data_types: List[str], run_id: str, geometry: Optional[Any] = None, bbox: Optional[List[float]] = None
) -> dict:
    runs_dir = Path("runs") / run_id / "outputs" / "vector"
    runs_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    
    try:
        import osmnx as ox
        import geopandas as gpd
        from shapely.geometry import shape, mapping
        
        ox.settings.use_cache = True
        ox.settings.log_console = False
        loop = asyncio.get_running_loop()
        
        async def _export_gdf(gdf_to_export, type_name):
            output_geojson = runs_dir / f"{region_name.replace(' ', '_')}_{type_name}.geojson"
            await loop.run_in_executor(None, gdf_to_export.to_file, str(output_geojson), "GeoJSON")
            results[f"vector_{type_name}_geojson"] = str(output_geojson)
            
            shp_gdf = gdf_to_export.copy()
            for col in shp_gdf.columns:
                if shp_gdf[col].dtype == object and col != "geometry":
                    shp_gdf[col] = shp_gdf[col].astype(str)
            
            gpkg_path = runs_dir / f"{region_name.replace(' ', '_')}_{type_name}.gpkg"
            def _write_gpkg():
                shp_gdf.to_file(str(gpkg_path), driver="GPKG")
            await loop.run_in_executor(None, _write_gpkg)
            results[f"vector_{type_name}_gpkg"] = str(gpkg_path)

        if "boundary" in data_types:
            try:
                gdf = await loop.run_in_executor(None, ox.geocode_to_gdf, region_name)
            except Exception as e:
                logger.warning(f"Failed geocoding to gdf for {region_name}: {e}")
                if geometry:
                    gdf = gpd.GeoDataFrame(index=[0], crs="epsg:4326", geometry=[shape(geometry)])
                else:
                    raise
            
            await _export_gdf(gdf, "boundary")
            results["geometry"] = mapping(gdf.unary_union)
            results["bbox"] = list(gdf.total_bounds)
            
        if "features" in data_types:
            tags = {'building': True, 'highway': True, 'waterway': True, 'natural': True, 'amenity': True}
            if geometry:
                try:
                    geom = shape(geometry)
                    if geom.geom_type in ["Point", "LineString"]:
                        geom = geom.buffer(0.01)
                    features = await loop.run_in_executor(None, ox.features_from_polygon, geom, tags)
                except Exception as e:
                    logger.warning(f"Failed to fetch features by geometry: {e}. Falling back to region_name.")
                    features = await loop.run_in_executor(None, ox.features_from_place, region_name, tags)
            else:
                features = await loop.run_in_executor(None, ox.features_from_place, region_name, tags)
                
            await _export_gdf(features, "features")
            
    except Exception as e:
        logger.error(f"Vector fetch failed: {e}")
        raise Exception(f"Failed to fetch vector data: {str(e)}")

    return results
