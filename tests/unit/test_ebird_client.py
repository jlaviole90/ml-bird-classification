"""Unit tests for the eBird API client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from catalog.api.ebird.client import EBirdClient


@pytest.fixture
def client():
    return EBirdClient(
        api_key="test-key",
        region="US-NY-109",
        lat=42.45,
        lng=-76.50,
        radius_km=25,
    )


def _mock_response(data, status_code=200):
    """Build a mock httpx.Response that behaves like a real one."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


class TestEBirdClientInit:
    def test_stores_config(self, client):
        assert client.api_key == "test-key"
        assert client.region == "US-NY-109"
        assert client.lat == 42.45
        assert client.lng == -76.50

    def test_sets_auth_header(self, client):
        assert client._client.headers["X-eBirdApiToken"] == "test-key"

    def test_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("EBIRD_API_KEY", "env-key")
        monkeypatch.setenv("EBIRD_REGION", "US-CA-037")
        monkeypatch.setenv("EBIRD_LAT", "34.05")
        monkeypatch.setenv("EBIRD_LNG", "-118.25")
        c = EBirdClient()
        assert c.api_key == "env-key"
        assert c.region == "US-CA-037"
        assert c.lat == 34.05


class TestEBirdClientEndpoints:
    @pytest.mark.asyncio
    async def test_get_recent_observations(self, client):
        mock_data = [{"speciesCode": "amerob", "comName": "American Robin"}]
        client._client.get = AsyncMock(return_value=_mock_response(mock_data))

        result = await client.get_recent_observations()
        assert result == mock_data
        client._client.get.assert_called_once()
        call_path = client._client.get.call_args[0][0]
        assert "US-NY-109" in call_path

    @pytest.mark.asyncio
    async def test_get_species_list(self, client):
        mock_data = ["amerob", "baleag", "carwre"]
        client._client.get = AsyncMock(return_value=_mock_response(mock_data))

        result = await client.get_species_list()
        assert result == mock_data

    @pytest.mark.asyncio
    async def test_get_notable_observations(self, client):
        mock_data = [{"speciesCode": "snobun", "comName": "Snow Bunting"}]
        client._client.get = AsyncMock(return_value=_mock_response(mock_data))

        result = await client.get_notable_observations()
        assert result == mock_data

    @pytest.mark.asyncio
    async def test_get_taxonomy(self, client):
        mock_data = [{"speciesCode": "amerob", "comName": "American Robin", "order": "Passeriformes"}]
        client._client.get = AsyncMock(return_value=_mock_response(mock_data))

        result = await client.get_taxonomy(species_codes=["amerob"])
        assert result[0]["order"] == "Passeriformes"
        call_params = client._client.get.call_args[1]["params"]
        assert "amerob" in call_params["species"]

    @pytest.mark.asyncio
    async def test_get_nearby_hotspots(self, client):
        mock_data = [{"locId": "L12345", "locName": "Central Park", "lat": 40.78, "lng": -73.97}]
        client._client.get = AsyncMock(return_value=_mock_response(mock_data))

        result = await client.get_nearby_hotspots()
        assert result[0]["locId"] == "L12345"

    @pytest.mark.asyncio
    async def test_get_region_stats(self, client):
        mock_data = {"numChecklists": 42, "numSpecies": 15}
        client._client.get = AsyncMock(return_value=_mock_response(mock_data))

        result = await client.get_region_stats()
        assert result["numChecklists"] == 42

    @pytest.mark.asyncio
    async def test_get_recent_species_observations(self, client):
        mock_data = [{"speciesCode": "amerob", "howMany": 3}]
        client._client.get = AsyncMock(return_value=_mock_response(mock_data))

        result = await client.get_recent_species_observations("amerob")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_nearby_observations(self, client):
        mock_data = [{"speciesCode": "amerob"}]
        client._client.get = AsyncMock(return_value=_mock_response(mock_data))

        result = await client.get_nearby_observations()
        assert result == mock_data

    @pytest.mark.asyncio
    async def test_api_error_raises(self, client):
        error_resp = MagicMock(spec=httpx.Response)
        error_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403 Forbidden",
            request=httpx.Request("GET", "http://test"),
            response=httpx.Response(403),
        )
        client._client.get = AsyncMock(return_value=error_resp)

        with pytest.raises(httpx.HTTPStatusError):
            await client.get_species_list()
