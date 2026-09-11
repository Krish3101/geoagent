import numpy as np
import pytest

from app.geo import raster


def test_compute_ndvi_formula_and_constraints():
    nir = np.array([[1000, 2000], [500, 0]], dtype=np.int32)
    red = np.array([[500, 2000], [1000, 0]], dtype=np.int32)

    # Expected:
    # (1000 - 500) / (1000 + 500) = 500 / 1500 = 0.333333
    # (2000 - 2000) / (2000 + 2000) = 0 / 4000 = 0.0
    # (500 - 1000) / (500 + 1000) = -500 / 1500 = -0.333333
    # 0 / 0 = nodata -9999.0

    ndvi = raster.compute_ndvi_array(nir, red)

    assert ndvi.dtype == np.float32
    assert pytest.approx(ndvi[0, 0], 1e-4) == 0.333333
    assert pytest.approx(ndvi[0, 1], 1e-4) == 0.0
    assert pytest.approx(ndvi[1, 0], 1e-4) == -0.333333
    assert ndvi[1, 1] == -9999.0

    # Valid values are strictly in [-1, 1]
    valid_mask = ndvi != -9999.0
    assert np.all(ndvi[valid_mask] >= -1.0)
    assert np.all(ndvi[valid_mask] <= 1.0)


def test_estimate_resolution_adaptive_stepping():
    # Small box: ~1km x ~1km -> 10m has (1000/10) * (1000/10) = 10,000 pixels <= 40M -> 10m
    small_bbox = [0.0, 0.0, 0.01, 0.01]
    res = raster.estimate_resolution(small_bbox, max_pixels=40_000_000)
    assert res == 10

    # Intermediate box: ~100km x ~100km -> 10m has (100000/10)^2 = 100,000,000 > 40M.
    # At 20m: (100000/20)^2 = 5000^2 = 25,000,000 <= 40M -> 20m
    med_bbox = [0.0, 0.0, 0.9, 0.9]
    res_med = raster.estimate_resolution(med_bbox, max_pixels=40_000_000)
    assert res_med == 20

    # Very large area: thousands of km -> exceeds even 60m -> raises ValueError
    giant_bbox = [-120.0, 20.0, -70.0, 50.0]
    with pytest.raises(ValueError, match="requires too many pixels even at 60m"):
        raster.estimate_resolution(giant_bbox, max_pixels=40_000_000)
