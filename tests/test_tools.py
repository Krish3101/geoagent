import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from src.tools.geocoding import geocode_location
from src.tools.raster import fetch_raster_data

def test_raster_requires_spatial_constraint():
    """FR-14: fetch_raster raises ValueError if neither bbox nor geometry is provided."""
    async def run():
        with pytest.raises(ValueError) as exc_info:
            await fetch_raster_data(
                region_name="Nowhere",
                start_date="2024-01-01",
                end_date="2024-01-31",
                data_types=["true_color"],
                run_id="test_run",
                bbox=None,
                geometry=None
            )
        assert "Spatial context must be established first" in str(exc_info.value)

    asyncio.run(run())

def test_geocode_relaxation_success():
    """FR-7: Geocoding retries with progressive query relaxation."""
    async def run():
        mock_resp_empty = MagicMock()
        mock_resp_empty.json.return_value = []
        mock_resp_empty.raise_for_status = MagicMock()

        mock_resp_hit = MagicMock()
        mock_resp_hit.json.return_value = [{
            "boundingbox": ["40.76", "40.78", "-73.97", "-73.95"],
            "geojson": {"type": "Polygon", "coordinates": []}
        }]
        mock_resp_hit.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            # First word query fails, second relaxed query succeeds
            mock_get.side_effect = [mock_resp_empty, mock_resp_hit]
            
            result = await geocode_location("The Great Central Park")
            assert result["resolved_location"] == "Great Central Park"
            assert result["bbox"] == [-73.97, 40.76, -73.95, 40.78]
            assert "geometry" in result

    asyncio.run(run())

def test_geocode_exhaustion_failure():
    """FR-7: Reports error when all query relaxations fail."""
    async def run():
        mock_resp_empty = MagicMock()
        mock_resp_empty.json.return_value = []
        mock_resp_empty.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp_empty
            with pytest.raises(ValueError) as exc_info:
                await geocode_location("Nonexistent place 1234567")
            assert "Could not geocode location" in str(exc_info.value)

    asyncio.run(run())
