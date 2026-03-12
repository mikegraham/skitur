FROM python:3.13-slim

# System libraries for rasterio (GDAL), shapely (GEOS), and opencv
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev \
    libgeos-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY skitur/ skitur/

RUN pip install --no-cache-dir . gunicorn

# Preload DEM tile catalogs at build time so first request is fast
RUN python -c "from dem_stitcher.datasets import get_global_dem_tile_extents; \
    [get_global_dem_tile_extents(d) for d in ('3dep', 'glo_30', 'glo_90_missing')]"

EXPOSE 8000

CMD ["gunicorn", "skitur.app:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
