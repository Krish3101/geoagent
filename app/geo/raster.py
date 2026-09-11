import math
from pathlib import Path
from typing import Any, Literal

import numpy as np
import planetary_computer
import pystac_client
import shapely.geometry

from app.config import settings

Product = Literal["true_color", "ndvi"]


def estimate_resolution(bbox: list[float], max_pixels: int) -> int:
    """Calculate the finest resolution (10m, 20m, 60m) that fits within max_pixels.

    Raises ValueError if even 60m exceeds max_pixels.
    """
    minx, miny, maxx, maxy = bbox
    mid_lat = (miny + maxy) / 2.0
    width_m = abs(maxx - minx) * 111320.0 * math.cos(math.radians(mid_lat))
    height_m = abs(maxy - miny) * 111320.0

    for res in [10, 20, 60]:
        pixels = (width_m / res) * (height_m / res)
        if pixels <= max_pixels:
            return res

    raise ValueError(
        "Requested area requires too many pixels even at 60m resolution. "
        "Please narrow your area of interest."
    )


def compute_ndvi_array(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    """Compute NDVI array with float32, nodata -9999.0, clipped to [-1.0, 1.0]."""
    nir = nir.astype(np.float32)
    red = red.astype(np.float32)
    denom = nir + red

    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = (nir - red) / denom
        ndvi = np.where(denom == 0, -9999.0, ndvi)
        ndvi = np.where(np.isnan(ndvi), -9999.0, ndvi)
        ndvi = np.where((ndvi != -9999.0) & (ndvi > 1.0), 1.0, ndvi)
        ndvi = np.where((ndvi != -9999.0) & (ndvi < -1.0), -1.0, ndvi)

    return ndvi.astype(np.float32)


def fetch_raster_product(
    aoi: dict[str, Any],
    start_date: str,
    end_date: str,
    product: Product,
    output_dir: Path,
    slug: str,
    max_cloud_cover: int = 20,
) -> tuple[dict[str, Any], int, str, float]:
    """Search STAC catalog, load Sentinel-2 imagery, compute product, and write GeoTIFF.

    Returns:
        (artifact_meta_dict, resolution_used, scene_date_str, cloud_cover_float)
    """
    import odc.stac

    bbox = aoi.get("bbox")
    geom_dict = aoi.get("geometry")
    if not bbox or not geom_dict:
        raise ValueError("AOI is missing bbox or geometry")

    resolution = estimate_resolution(bbox, settings.raster_max_pixels)

    stac_api_url = "https://planetarycomputer.microsoft.com/api/stac/v1"
    catalog = pystac_client.Client.open(stac_api_url, modifier=planetary_computer.sign_inplace)

    search = catalog.search(
        collections=["sentinel-2-l2a"],
        intersects=geom_dict,
        datetime=f"{start_date}/{end_date}",
        query={"eo:cloud_cover": {"lt": max_cloud_cover}},
        sortby=[{"field": "properties.eo:cloud_cover", "direction": "asc"}],
    )

    items = list(search.items())
    if not items:
        raise ValueError(
            f"No imagery found for date range {start_date} to {end_date} "
            f"with cloud cover <= {max_cloud_cover}%."
        )

    # Group by solar day and select the day with the lowest average cloud cover
    day_groups: dict[str, list[Any]] = {}
    for item in items:
        day = item.datetime.strftime("%Y-%m-%d") if item.datetime else "unknown"
        day_groups.setdefault(day, []).append(item)

    best_day = min(
        day_groups.keys(),
        key=lambda d: (
            sum(i.properties.get("eo:cloud_cover", 100.0) for i in day_groups[d])
            / len(day_groups[d])
        ),
    )
    best_items = day_groups[best_day]
    avg_cloud = sum(i.properties.get("eo:cloud_cover", 0.0) for i in best_items) / len(best_items)
    scene_ids = [i.id for i in best_items]

    bands = ["B04", "B08"] if product == "ndvi" else ["visual"]

    ds = odc.stac.load(
        best_items,
        bands=bands,
        resolution=resolution,
        chunks={},
        groupby="solar_day",
        bbox=bbox,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / f"{slug}_{product}.tif"

    # Clip to exact AOI geometry using rioxarray
    polygon = shapely.geometry.shape(geom_dict)
    ds = ds.rio.clip([polygon], crs="EPSG:4326", all_touched=True)

    crs_str = str(ds.rio.crs or "EPSG:4326")

    if product == "ndvi":
        nir = ds["B08"].isel(time=0).values
        red = ds["B04"].isel(time=0).values
        ndvi_arr = compute_ndvi_array(nir, red)

        # Write using rioxarray DataArray
        da = ds["B04"].isel(time=0).copy(data=ndvi_arr)
        da.rio.write_nodata(-9999.0, inplace=True)
        da.rio.to_raster(out_file, driver="GTiff", dtype="float32")
    else:
        # true_color uses visual (RGB)
        vis = ds["visual"].isel(time=0)
        vis.rio.to_raster(out_file, driver="GTiff")

    artifact = {
        "kind": "raster",
        "filename": out_file.name,
        "filepath": out_file,
        "size_bytes": out_file.stat().st_size,
        "bounds": [round(b, 6) for b in bbox],
        "meta": {
            "product": product,
            "crs": crs_str,
            "resolution_m": resolution,
            "cloud_cover": round(avg_cloud, 1),
            "scene_ids": scene_ids,
            "date": best_day,
        },
    }

    return artifact, resolution, best_day, avg_cloud
