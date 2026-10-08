import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import planetary_computer
import pystac_client
import rasterio
import rioxarray  # noqa: F401
import shapely.geometry

from app.config import RASTER_MAX_PIXELS
from app.geo.types import GeoArtifact, NDVIResult, unique_stem

# Set GDAL timeouts
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "30")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "2")


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


BOA_ADD_OFFSET = 1000


def reflectance_offset(item: Any) -> int:
    """The offset to subtract from this scene's band values: 1000 from baseline 04.00, else 0."""
    try:
        baseline = float(item.properties.get("s2:processing_baseline", "0"))
    except (TypeError, ValueError):
        return 0
    return BOA_ADD_OFFSET if baseline >= 4.0 else 0


def compute_ndvi_array(nir: np.ndarray, red: np.ndarray, offset: int = 0) -> np.ndarray:
    """Compute NDVI as float32, clipped to [-1.0, 1.0], with -9999.0 for no data.

    offset is subtracted from both bands first (see BOA_ADD_OFFSET). A band value of 0 is
    Sentinel-2's no-data value, so those pixels come out as -9999.0.
    """
    no_data = (nir == 0) | (red == 0)
    nir = nir.astype(np.float32) - offset
    red = red.astype(np.float32) - offset
    denom = nir + red

    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = np.clip((nir - red) / denom, -1.0, 1.0)

    ndvi = np.where(no_data | (denom == 0) | np.isnan(ndvi), -9999.0, ndvi)
    return ndvi.astype(np.float32)


def colorize_ndvi_to_rgba(
    ndvi_arr: np.ndarray, nodata: float = -9999.0
) -> tuple[np.ndarray, float]:
    """Colorize a 2D NDVI array into a 4-channel (RGBA) uint8 image array and compute mean."""
    h, w = ndvi_arr.shape
    rgba = np.zeros((4, h, w), dtype=np.uint8)

    valid = (ndvi_arr != nodata) & (~np.isnan(ndvi_arr)) & (ndvi_arr >= -1.0) & (ndvi_arr <= 1.0)
    mean_ndvi = float(np.mean(ndvi_arr[valid])) if np.any(valid) else 0.0

    # Stepped color ramp
    classes = [
        (valid & (ndvi_arr < 0.0), (91, 112, 131)),
        (valid & (ndvi_arr >= 0.0) & (ndvi_arr < 0.15), (140, 81, 10)),
        (valid & (ndvi_arr >= 0.15) & (ndvi_arr < 0.30), (216, 179, 101)),
        (valid & (ndvi_arr >= 0.30) & (ndvi_arr < 0.50), (199, 234, 229)),
        (valid & (ndvi_arr >= 0.50) & (ndvi_arr < 0.70), (90, 180, 172)),
        (valid & (ndvi_arr >= 0.70), (1, 102, 94)),
    ]

    for mask, (r, g, b) in classes:
        rgba[0][mask] = r
        rgba[1][mask] = g
        rgba[2][mask] = b
        rgba[3][mask] = 255  # opaque

    return rgba, round(mean_ndvi, 2)


def fetch_ndvi_product(
    aoi: dict[str, Any],
    start_date: str,
    end_date: str,
    output_dir: Path,
    slug: str,
    max_cloud_cover: int = 20,
    task_id: str = "",
) -> NDVIResult:
    """Search STAC catalog, load Sentinel-2 imagery, compute NDVI,
    and write GeoTIFF + PNG preview.
    """
    import odc.stac

    bbox = aoi.get("bbox")
    geom_dict = aoi.get("geometry")
    if not bbox or not geom_dict:
        raise ValueError("AOI is missing bbox or geometry")

    resolution = estimate_resolution(bbox, RASTER_MAX_PIXELS)

    stac_api_url = "https://planetarycomputer.microsoft.com/api/stac/v1"
    catalog = pystac_client.Client.open(
        stac_api_url,
        modifier=planetary_computer.sign_inplace,
        timeout=30,
    )

    search = catalog.search(
        collections=["sentinel-2-l2a"],
        intersects=geom_dict,
        datetime=f"{start_date}/{end_date}",
        query={"eo:cloud_cover": {"lt": max_cloud_cover}},
        sortby=[{"field": "properties.eo:cloud_cover", "direction": "asc"}],
        max_items=200,
    )

    items = list(search.items())
    if not items:
        raise ValueError(
            f"No imagery found for date range {start_date} to {end_date} "
            f"with cloud cover <= {max_cloud_cover}%."
        )

    # Group by solar day and select the day with lowest avg cloud cover
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

    ds = odc.stac.load(
        best_items,
        bands=["B04", "B08"],
        resolution=resolution,
        chunks={},
        groupby="solar_day",
        bbox=bbox,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = unique_stem(output_dir, f"{slug}_ndvi", (".tif", "_preview.png"))
    out_file = output_dir / f"{stem}.tif"
    preview_file = output_dir / f"{stem}_preview.png"

    polygon = shapely.geometry.shape(geom_dict)
    ds = ds.rio.clip([polygon], crs="EPSG:4326", all_touched=True)

    crs_str = str(ds.rio.crs or "EPSG:4326")

    nir = ds["B08"].isel(time=0).values
    red = ds["B04"].isel(time=0).values
    ndvi_arr = compute_ndvi_array(nir, red, offset=reflectance_offset(best_items[0]))

    # Write GeoTIFF
    da = ds["B04"].isel(time=0).copy(data=ndvi_arr)
    da.rio.write_nodata(-9999.0, inplace=True)
    da.rio.to_raster(out_file, driver="GTiff", dtype="float32")

    # Generate colorized PNG preview reprojected to EPSG:4326
    try:
        da_4326 = da.rio.reproject("EPSG:4326")
        reprojected_ndvi = da_4326.values
        preview_bounds = [round(b, 6) for b in da_4326.rio.bounds()]
    except (ValueError, RuntimeError, rasterio.errors.RasterioError):
        reprojected_ndvi = ndvi_arr
        preview_bounds = [round(b, 6) for b in bbox]

    # Downsample preview if needed so max side <= 1024
    ph, pw = reprojected_ndvi.shape
    step = max(1, math.ceil(max(ph, pw) / 1024))
    if step > 1:
        reprojected_ndvi = reprojected_ndvi[::step, ::step]
        ph, pw = reprojected_ndvi.shape

    rgba, mean_ndvi = colorize_ndvi_to_rgba(reprojected_ndvi)
    with rasterio.open(
        preview_file,
        "w",
        driver="PNG",
        height=ph,
        width=pw,
        count=4,
        dtype="uint8",
    ) as dst:
        dst.write(rgba)

    prefix = f"runs/{task_id}/raster" if task_id else ""
    rel_path = f"{prefix}/{out_file.name}" if prefix else out_file.name
    preview_rel_path = f"{prefix}/{preview_file.name}" if prefix else preview_file.name

    artifact = GeoArtifact(
        kind="raster",
        filename=out_file.name,
        relative_path=rel_path,
        size_bytes=out_file.stat().st_size,
        bounds=[round(b, 6) for b in bbox],
        meta={
            "product": "ndvi",
            "crs": crs_str,
            "resolution_m": resolution,
            "cloud_cover": round(avg_cloud, 1),
            "scene_ids": scene_ids,
            "date": best_day,
            "mean_ndvi": mean_ndvi,
            "preview_bounds": preview_bounds,
            "preview_filename": preview_file.name,
        },
    )

    preview_artifact = GeoArtifact(
        kind="raster",
        filename=preview_file.name,
        relative_path=preview_rel_path,
        size_bytes=preview_file.stat().st_size,
        bounds=preview_bounds,
        meta={
            "product": "ndvi_preview",
            "date": best_day,
            "mean_ndvi": mean_ndvi,
        },
    )

    return NDVIResult(
        artifact=artifact,
        preview_artifact=preview_artifact,
        resolution=resolution,
        scene_date=best_day,
        cloud_cover=avg_cloud,
    )
