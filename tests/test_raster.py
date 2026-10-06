import numpy as np
import pytest

from app.geo import ndvi


def test_compute_ndvi_formula_and_constraints():
    nir = np.array([[1000, 2000], [500, 0]], dtype=np.int32)
    red = np.array([[500, 2000], [1000, 0]], dtype=np.int32)

    # Expected:
    # (1000 - 500) / (1000 + 500) = 500 / 1500 = 0.333333
    # (2000 - 2000) / (2000 + 2000) = 0 / 4000 = 0.0
    # (500 - 1000) / (500 + 1000) = -500 / 1500 = -0.333333
    # 0 / 0 = nodata -9999.0

    result_ndvi = ndvi.compute_ndvi_array(nir, red)

    assert result_ndvi.dtype == np.float32
    assert pytest.approx(result_ndvi[0, 0], 1e-4) == 0.333333
    assert pytest.approx(result_ndvi[0, 1], 1e-4) == 0.0
    assert pytest.approx(result_ndvi[1, 0], 1e-4) == -0.333333
    assert result_ndvi[1, 1] == -9999.0

    # Valid values are strictly in [-1, 1]
    valid_mask = result_ndvi != -9999.0
    assert np.all(result_ndvi[valid_mask] >= -1.0)
    assert np.all(result_ndvi[valid_mask] <= 1.0)


def test_ndvi_removes_the_reflectance_offset():
    # Reflectance 0.2 (NIR) and 0.05 (red) is NDVI 0.6. Since baseline 04.00 they are
    # stored as 3000 and 1500, which without the offset would give 0.333.
    nir = np.array([[3000]], dtype=np.uint16)
    red = np.array([[1500]], dtype=np.uint16)

    result_ndvi = ndvi.compute_ndvi_array(nir, red, offset=1000)

    assert pytest.approx(result_ndvi[0, 0], 1e-4) == 0.6


def test_offset_depends_on_the_processing_baseline():
    class Item:
        def __init__(self, baseline):
            self.properties = {"s2:processing_baseline": baseline}

    assert ndvi.reflectance_offset(Item("05.11")) == 1000
    assert ndvi.reflectance_offset(Item("04.00")) == 1000
    assert ndvi.reflectance_offset(Item("03.01")) == 0


def test_estimate_resolution_adaptive_stepping():
    # Small box: ~1km x ~1km -> 10m has (1000/10) * (1000/10) = 10,000 pixels <= 25M -> 10m
    small_bbox = [0.0, 0.0, 0.01, 0.01]
    res = ndvi.estimate_resolution(small_bbox, max_pixels=25_000_000)
    assert res == 10

    # Intermediate box: ~89km x ~89km -> 10m has ~79M pixels > 25M.
    # At 20m: (89000/20)^2 = ~19.8M <= 25M -> 20m
    med_bbox = [0.0, 0.0, 0.8, 0.8]
    res_med = ndvi.estimate_resolution(med_bbox, max_pixels=25_000_000)
    assert res_med == 20

    # Very large area: thousands of km -> exceeds even 60m -> raises ValueError
    giant_bbox = [-120.0, 20.0, -70.0, 50.0]
    with pytest.raises(ValueError, match="requires too many pixels even at 60m"):
        ndvi.estimate_resolution(giant_bbox, max_pixels=25_000_000)


def test_colorize_ndvi_to_rgba():
    # Array with nodata, water (<0), soil (0.1), and vegetation (0.8)
    arr = np.array([[-9999.0, -0.2], [0.1, 0.8]], dtype=np.float32)
    rgba, mean_val = ndvi.colorize_ndvi_to_rgba(arr)

    assert rgba.shape == (4, 2, 2)
    assert rgba.dtype == np.uint8

    # Nodata pixel is transparent (alpha = 0)
    assert rgba[3, 0, 0] == 0

    # Water pixel is opaque (alpha = 255) with slate color (91, 112, 131)
    assert rgba[3, 0, 1] == 255
    assert rgba[0, 0, 1] == 91
    assert rgba[1, 0, 1] == 112
    assert rgba[2, 0, 1] == 131

    # Dense canopy (0.8) is opaque deep green (1, 102, 94)
    assert rgba[3, 1, 1] == 255
    assert rgba[0, 1, 1] == 1
    assert rgba[1, 1, 1] == 102
    assert rgba[2, 1, 1] == 94

    # Mean computed only over valid pixels: (-0.2 + 0.1 + 0.8) / 3 = 0.7 / 3 = 0.2333
    assert pytest.approx(mean_val, 0.05) == 0.23
