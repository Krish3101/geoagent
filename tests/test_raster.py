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
