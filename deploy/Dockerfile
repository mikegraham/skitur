FROM python:3.13-slim

# System libraries for rasterio (GDAL) and shapely (GEOS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev \
    libgeos-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY skitur/ skitur/
COPY deploy/config/ deploy/config/
COPY scripts/ scripts/
COPY _preseed_trail_counts.json ./
COPY deploy/docker-entrypoint.sh ./

RUN pip install --no-cache-dir . gunicorn requests

# Preload DEM tile catalogs at build time so first request is fast
RUN python -c "from dem_stitcher.datasets import get_global_dem_tile_extents; \
    [get_global_dem_tile_extents(d) for d in ('3dep', 'glo_30', 'glo_90_missing')]"

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gunicorn", "skitur.app:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "600"]
