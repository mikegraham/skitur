# 052: Improve DEM Data Sources
**Status:** OPEN
**Priority:** P2

## Description
Investigate and integrate better DEM data sources, especially higher-resolution options for Europe and other popular ski touring regions. Replace the SRTM global fallback with more accurate modern data.

## Current Sources
1. **USGS 3DEP** (via `seamless-3dep`) — 10m resolution, US only (CONUS, Alaska, Hawaii)
2. **NASA SRTM** (via `srtm.py`) — 30m resolution, global fallback (60N–60S only)

## Problems with Current Setup
- SRTM is from 2000, has known artifacts in steep terrain
- No coverage above 60N (misses northern Norway, Iceland, northern Sweden)
- 30m global resolution is coarse for slope analysis
- SRTM point-by-point lookup in `terrain.py` lines 186-194 is slow compared to tile-based approaches

## Research Results

### Best Single Upgrade: Copernicus GLO-30 (replaces SRTM)
- **Resolution**: 30m (1 arc-second) globally
- **Coverage**: Pole-to-pole (fixes the 60N gap)
- **Source**: TanDEM-X SAR mission (2011-2015)
- **Vertical accuracy**: ~2m RMSE in most areas (vs ~6m for SRTM). Studies confirm better performance in mountains (Alps, Andes)
- **License**: Free, open access
- **Access**:
  - AWS S3 public bucket: `s3://copernicus-dem-30m/` (no auth needed)
  - OpenTopography API via `bmi-topography` (PyPI) — fetches by bounding box, returns GeoTIFF. Rate limited: 100-300 calls/day with free API key
  - Microsoft Planetary Computer — STAC catalog via `pystac-client` + `rioxarray`, no rate limit for COGs
- **Note**: This is a DSM (includes canopy/buildings) but above treeline it's equivalent to bare earth

### FABDEM — Best Global Bare-Earth Option
- **Resolution**: 30m
- **Coverage**: Global (60S-80N)
- **What it is**: Copernicus GLO-30 with ML-based removal of tree canopy and building heights → true DTM
- **Vertical accuracy**: Mean absolute error 2.88m (vs 5.15m for raw Copernicus in forests). In dense canopy: 0.45m median error vs 12.95m
- **License**: CC-BY-4.0
- **Python**: `fabdem` package on PyPI — downloads tiles by bounding box
- **Best for**: Forested approach zones where canopy throws off slope calculations

### European High-Resolution Sources (finer than 30m)

#### Copernicus EEA-10 (10m, 39 European countries)
- Would be ideal but access is restricted to "Public Authorities" and eligible users
- Worth monitoring — restriction may loosen

#### National LiDAR Programs (the real goldmine)

| Country | Resolution | License | Access |
|---------|-----------|---------|--------|
| **Norway** | **1m** (DTM1) full country | CC-BY-4.0 | hoydedata.no |
| **Switzerland** | **2m** (SwissALTI3D) | Free since 2021 | swisstopo |
| **France** | **1-5m** (IGN RGE ALTI) | Open | IGN |
| **Austria** | **0.5-1m** various states | Free | State agencies |
| **Italy (South Tyrol)** | **2.5m** | Open | Province portals |

#### Sonny's LiDAR DTMs of Europe
- Community compilation aggregating national LiDAR from 18+ European countries
- Resampled to consistent formats (10m, 0.5 arc-second)
- Available at `sonny.4lima.de`
- Could be a single integration point for European coverage

#### ArcticDEM (2m, >60N latitude)
- Covers northern Norway (Lofoten, Lyngen, Troms), Iceland, northern Sweden/Finland, Svalbard
- Free, open access
- Python: `pdemtools` (PyPI)

### Other Regional High-Resolution Sources

| Region | Source | Resolution | License | Python Access |
|--------|--------|-----------|---------|---------------|
| **Japan** | GSI DEM5A | 5m (LiDAR, ~70% of country) | Free w/ registration | `fgd.gsi.go.jp` |
| **Japan** | GSI DEM10B | 10m (full country) | Free w/ registration | `fgd.gsi.go.jp` |
| **New Zealand** | LINZ LiDAR | 1m | CC-BY-4.0 | AWS `s3://nz-elevation` |

### Sources Evaluated and Rejected

| Source | Resolution | Why Skip |
|--------|-----------|----------|
| **ASTER GDEM v3** | 30m | Worst accuracy of 30m global options, known noise in mountains |
| **NASADEM** | 30m | Marginal improvement over SRTM, Copernicus is strictly better |
| **ALOS AW3D30** | 30m | Decent but Copernicus generally better accuracy |
| **TanDEM-X 12m** | 12m | Requires research proposal to DLR, not practical for public app |
| **MERIT DEM** | 90m | Too coarse, designed for hydrology not terrain analysis |

## dem-stitcher — Recommended Integration Library

**`dem-stitcher`** ([GitHub](https://github.com/ACCESS-Cloud-Based-InSAR/dem-stitcher), [PyPI](https://pypi.org/project/dem-stitcher/)) is the strongest candidate for replacing both `seamless-3dep` and `srtm.py` with a single library. It supports both Copernicus GLO-30 and USGS 3DEP.

### API

```python
from dem_stitcher import stitch_dem

# Copernicus GLO-30 (global, 30m)
dem, profile = stitch_dem(bounds=(-119.9, 34.4, -119.6, 34.8), dem_name='glo_30')

# USGS 3DEP (US only, 10m)
dem, profile = stitch_dem(bounds=(-119.9, 34.4, -119.6, 34.8), dem_name='3dep')
```

Returns `(np.ndarray, rasterio_profile_dict)` in EPSG:4326. Clean numpy array with NaN for nodata.

### Supported DEMs

| `dem_name` | Source | Resolution | Coverage |
|---|---|---|---|
| `glo_30` | Copernicus GLO-30 (AWS S3) | ~30m | Global |
| `glo_90` | Copernicus GLO-90 (AWS S3) | ~90m | Global |
| `3dep` | USGS 3DEP 1/3 arc-second | ~10m | CONUS, Hawaii, PR, limited Alaska |

SRTM and NASADEM were removed in v2.5.13 (Feb 2026) because LP DAAC discontinued hosting. GLO-30 is the recommended replacement.

### Caching

**No automatic cache.** Must pass `dst_tile_dir=/path/to/cache` or it re-downloads every call. With `dst_tile_dir` set, existing tiles are reused (controlled by `overwrite_existing_tiles=False` default).

**Geoid gotcha**: EGM-08 geoid (needed for Copernicus) is fetched from S3 every call. Workaround: download once and pass `geoid_path=` parameter. EGM-96 and Geoid-18 are bundled locally.

### Integration into terrain.py

For our web app, we'd need:
1. Set `dst_tile_dir=~/.cache/skitur/dem-stitcher/` for persistent caching
2. Pre-download EGM-08 geoid and pass via `geoid_path`
3. Resolution cascade: try `3dep` for US bounds, fall back to `glo_30` globally
4. Convert output numpy array to xarray DataArray (to match existing `_dem_cache` structure)

### Maturity Assessment

- **Maintainer**: NASA JPL (Charlie Marshak), under ACCESS Cloud-Based InSAR initiative
- **License**: Apache-2.0
- **Latest release**: v2.5.13 (Feb 9, 2026). ~monthly/quarterly releases since 2023
- **Community**: 539 commits, 59 stars, 17 forks, 5 open issues
- **Assessment**: Production-quality niche tool. Used in JPL's InSAR pipeline, not a weekend project. The small community (59 stars) means some single-maintainer risk, but the maintainer is responsive and adapts quickly to upstream changes (e.g., removed SRTM within weeks of LP DAAC shutdown). API has been stable at 2.5.x for over a year. Comparable in maturity to `seamless-3dep` (which we already depend on).

### Dependencies

Most already in our project: numpy, rasterio, shapely, geopandas, pyproj, requests, affine. Only new dep: `boto3` (~15MB). Package itself is 29MB (bundled tile indices + geoid files).

## Other Python Libraries (for reference)

| Library | PyPI Package | Accesses | Resolution | Coverage |
|---------|-------------|----------|------------|----------|
| `seamless-3dep` | seamless-3dep | USGS 3DEP | 10m | US |
| `srtm.py` | SRTM.py | NASA SRTM | 30m | 60N-60S |
| `bmi-topography` | bmi-topography | OpenTopography API | 30m+ | Global |
| `fabdem` | fabdem | FABDEM bare-earth | 30m | Global |
| `pdemtools` | pdemtools | ArcticDEM | 2m | Arctic |
| `rioxarray` | rioxarray | Any COG/GeoTIFF | varies | varies |
| `pystac-client` | pystac-client | STAC catalogs | varies | varies |

## Recommended Implementation Path

### Phase 1: Replace both DEM libraries with dem-stitcher
- Single library for both US (3DEP 10m) and global (Copernicus GLO-30)
- Replaces `seamless-3dep` AND `srtm.py` with unified API
- Add local tile caching via `dst_tile_dir`
- Pre-download and bundle EGM-08 geoid for offline reliability
- Biggest win: 3x better global accuracy, coverage above 60N, simpler codebase

### Phase 2: Add European national LiDAR cascade
- Norway 1m, Switzerland 2m, France 1-5m, Austria 0.5-1m
- Sonny's LiDAR compilation as fallback/unified source
- Resolution cascade: try national LiDAR first, fall back to Copernicus GLO-30

### Phase 3: Add other regional sources
- ArcticDEM 2m for >60N
- Japan GSI 5m/10m
- New Zealand LINZ 1m

### Full Resolution Cascade (target)
1. **US**: 3DEP 10m (keep current)
2. **Norway**: DTM1 1m
3. **Switzerland**: SwissALTI3D 2m
4. **France**: IGN RGE ALTI 1-5m
5. **Austria**: State LiDAR 0.5-1m
6. **Arctic >60N** (if not covered above): ArcticDEM 2m
7. **Japan**: GSI 5m
8. **New Zealand**: LINZ 1m
9. **Everywhere else**: Copernicus GLO-30 (replacing SRTM)

## Copernicus GLO-30 vs SRTM — Why Replace?

Both are 30m global DEMs, but Copernicus is strictly better for our use case:

| | SRTM | Copernicus GLO-30 |
|---|---|---|
| **Year** | 2000 | 2011-2015 |
| **Vertical accuracy** | ~6m RMSE | ~2m RMSE |
| **Coverage** | 60N–60S only | Pole to pole |
| **Mountain accuracy** | Known artifacts in steep faces | Better in complex topography (validated in Alps, Andes) |
| **Sensor** | C-band radar (single-pass) | X-band SAR (multi-pass, averaged) |
| **Voids** | Many in steep mountain faces, filled with interpolation | Very few voids |
| **Access pattern** | Point-by-point via `srtm.py` (slow loop in terrain.py:186-194) | Tile-based GeoTIFF from AWS S3 (fast, no auth) |
| **Glaciers/snow** | Captures 2000 snowpack/glacier state | Captures 2011-2015 state (more recent) |
| **Auth required** | No | No (AWS S3 public bucket) |

**The only argument for SRTM**: `srtm.py` is a one-liner install with zero configuration. But Copernicus on AWS (`s3://copernicus-dem-30m/`) also needs no auth — just `rioxarray` or `rasterio` to read COG tiles directly. The integration effort is comparable, and you get:
- 3x better vertical accuracy
- Coverage above 60N latitude (Lofoten, Lyngen, Iceland, Svalbard — all popular ski touring)
- Fewer void artifacts on steep faces (critical for slope analysis)
- Tile-based reads instead of the slow point-query loop

**Conclusion**: There is no technical reason to prefer SRTM. Copernicus GLO-30 dominates on accuracy, coverage, and access speed. The swap should be Phase 1.

## Notes on Snow Cover
No DEM handles seasonal snow variation — they all represent the surface at acquisition time, which may include snowpack. SAR-based DEMs (Copernicus/TanDEM-X) are less affected by cloud cover during acquisition than optical DEMs (ASTER/ALOS), which matters because mountain peaks are frequently cloudy.
