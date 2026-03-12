from pathlib import Path

import pytest

from skitur.terrain import TerrainLoader

_DEM_CACHE_DIR = Path.home() / ".cache" / "skitur" / "dem"


@pytest.fixture(scope="session")
def dem_cache_dir():
    """DEM tile cache directory for tests."""
    _DEM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _DEM_CACHE_DIR


@pytest.fixture(scope="session")
def terrain_loader(dem_cache_dir):
    """Shared TerrainLoader for tests."""
    return TerrainLoader(cache_dir=dem_cache_dir)
