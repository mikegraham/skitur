#!/usr/bin/env python3
"""Visualize OSM nordic way centers and selected DEM tiles.

Input locations are expected from `build_nordic_way_tile_manifest.py`
(`osm_type,osm_id,lat,lon,...`).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import colors

from dem_stitcher.datasets import get_global_dem_tile_extents


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render world maps for nordic way centers and DEM tile coverage.",
    )
    parser.add_argument(
        "--locations",
        required=True,
        help="CSV/Parquet with columns including lat, lon.",
    )
    parser.add_argument(
        "--tile-manifest",
        default=None,
        help="Optional CSV/Parquet manifest with tile_id, dem_name columns.",
    )
    parser.add_argument(
        "--countries-url",
        default="https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_0_countries.zip",
        help="Natural Earth countries dataset URL/path.",
    )
    parser.add_argument(
        "--us-states-url",
        default="https://naturalearth.s3.amazonaws.com/50m_cultural/ne_50m_admin_1_states_provinces.zip",
        help="Natural Earth admin-1 dataset URL/path for U.S. state outlines.",
    )
    parser.add_argument(
        "--world-heatmap-out",
        default="nordic_way_centers_world_heatmap.png",
        help="Output world heatmap PNG path.",
    )
    parser.add_argument(
        "--world-points-out",
        default="nordic_way_centers_world_points.png",
        help="Output world points PNG path.",
    )
    parser.add_argument(
        "--extent-out",
        default="nordic_way_centers_extent.png",
        help="Output auto-extent PNG path.",
    )
    return parser.parse_args()


def read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    raise ValueError(f"Unsupported file type for {path}; expected .csv or .parquet")


def load_locations(path: Path) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    df = read_table(path)
    if "lat" not in df.columns or "lon" not in df.columns:
        raise ValueError("Locations file must include `lat` and `lon` columns")
    df = df.copy()
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df = df.dropna(subset=["lat", "lon"])
    lats = df["lat"].to_numpy(dtype=float)
    lons = df["lon"].to_numpy(dtype=float)
    return lons, lats, df


def load_tile_geometries(tile_manifest: Path | None) -> gpd.GeoDataFrame:
    if tile_manifest is None:
        return gpd.GeoDataFrame({"tile_id": [], "dem_name": []}, geometry=[], crs="EPSG:4326")
    if not tile_manifest.exists():
        raise FileNotFoundError(tile_manifest)

    manifest = read_table(tile_manifest)
    if "tile_id" not in manifest.columns or "dem_name" not in manifest.columns:
        raise ValueError("Tile manifest must include `tile_id` and `dem_name` columns")

    frames: list[gpd.GeoDataFrame] = []
    for dem_name in sorted(set(manifest["dem_name"].astype(str))):
        ids = set(manifest.loc[manifest["dem_name"].astype(str) == dem_name, "tile_id"].astype(str))
        extents = get_global_dem_tile_extents(dem_name)
        extents = extents[extents["tile_id"].isin(ids)].copy()
        if extents.empty:
            continue
        frames.append(extents[["tile_id", "dem_name", "geometry"]])

    if not frames:
        return gpd.GeoDataFrame({"tile_id": [], "dem_name": []}, geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")


def _style_axis(ax: plt.Axes, title: str) -> None:
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(title)
    ax.grid(True, color="#5a6d7d", linewidth=0.35, alpha=0.3, zorder=6)
    for spine in ax.spines.values():
        spine.set_color("#73889a")
    ax.tick_params(colors="#dbe7f2")
    ax.title.set_color("#eef6ff")
    ax.xaxis.label.set_color("#dbe7f2")
    ax.yaxis.label.set_color("#dbe7f2")


def _draw_countries(ax: plt.Axes, countries: gpd.GeoDataFrame) -> None:
    countries.plot(
        ax=ax,
        facecolor="#24384a",
        edgecolor="#4a6477",
        linewidth=0.35,
        alpha=0.95,
        zorder=1,
    )


def _draw_us_states(ax: plt.Axes, us_states: gpd.GeoDataFrame) -> None:
    us_states.boundary.plot(
        ax=ax,
        color="#91a8bb",
        linewidth=0.45,
        alpha=0.72,
        zorder=2,
    )


def _draw_tiles(ax: plt.Axes, tiles: gpd.GeoDataFrame) -> None:
    if tiles.empty:
        return
    tiles.boundary.plot(ax=ax, color="#020617", linewidth=1.65, alpha=0.48, zorder=7)
    tiles.boundary.plot(ax=ax, color="#fde047", linewidth=1.0, alpha=0.98, zorder=8)


def render_world_heatmap(
    *,
    countries: gpd.GeoDataFrame,
    us_states: gpd.GeoDataFrame,
    tiles: gpd.GeoDataFrame,
    lons: np.ndarray,
    lats: np.ndarray,
    output_path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(18, 10), dpi=240)
    ax.set_facecolor("#081621")
    fig.patch.set_facecolor("#050d14")
    _draw_countries(ax, countries)
    _draw_us_states(ax, us_states)

    hb = ax.hexbin(
        lons,
        lats,
        gridsize=(220, 110),
        extent=(-180, 180, -90, 90),
        mincnt=1,
        cmap="magma",
        norm=colors.LogNorm(vmin=1),
        linewidths=0,
        alpha=0.9,
        zorder=3,
    )
    ax.scatter(lons, lats, s=2.0, color="#a7f3d0", alpha=0.15, linewidths=0, zorder=4)
    _draw_tiles(ax, tiles)

    ax.set_xlim(-180, 180)
    ax.set_ylim(-90, 90)
    _style_axis(ax, "OSM Nordic Ways Density (World)")

    cbar = fig.colorbar(hb, ax=ax, fraction=0.025, pad=0.02)
    cbar.set_label("Way centers per hex (log scale)", color="#dbe7f2")
    cbar.ax.yaxis.set_tick_params(color="#dbe7f2")
    plt.setp(cbar.ax.get_yticklabels(), color="#dbe7f2")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)


def render_world_points(
    *,
    countries: gpd.GeoDataFrame,
    us_states: gpd.GeoDataFrame,
    tiles: gpd.GeoDataFrame,
    lons: np.ndarray,
    lats: np.ndarray,
    output_path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(18, 10), dpi=240)
    ax.set_facecolor("#09151f")
    fig.patch.set_facecolor("#050d14")
    _draw_countries(ax, countries)
    _draw_us_states(ax, us_states)

    # Point glow + core so centers remain visible at world scale.
    ax.scatter(lons, lats, s=48, color="#67e8f9", alpha=0.06, linewidths=0, zorder=4)
    ax.scatter(lons, lats, s=6, color="#22d3ee", alpha=0.62, linewidths=0, zorder=5)
    _draw_tiles(ax, tiles)

    ax.set_xlim(-180, 180)
    ax.set_ylim(-90, 90)
    _style_axis(ax, "OSM Nordic Way Centers (World)")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)


def render_extent_points(
    *,
    countries: gpd.GeoDataFrame,
    us_states: gpd.GeoDataFrame,
    tiles: gpd.GeoDataFrame,
    lons: np.ndarray,
    lats: np.ndarray,
    output_path: Path,
    xlim: tuple[float, float],
    ylim: tuple[float, float],
) -> None:
    fig, ax = plt.subplots(figsize=(16, 10), dpi=240)
    ax.set_facecolor("#09151f")
    fig.patch.set_facecolor("#050d14")
    _draw_countries(ax, countries)
    _draw_us_states(ax, us_states)

    ax.scatter(lons, lats, s=30, color="#67e8f9", alpha=0.08, linewidths=0, zorder=4)
    ax.scatter(lons, lats, s=5, color="#22d3ee", alpha=0.7, linewidths=0, zorder=5)
    _draw_tiles(ax, tiles)

    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    _style_axis(ax, "OSM Nordic Way Centers (Auto-Zoomed Extent)")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)


def main() -> int:
    args = parse_args()
    locations_path = Path(args.locations)
    if not locations_path.exists():
        raise FileNotFoundError(locations_path)

    lons, lats, frame = load_locations(locations_path)
    if len(frame) == 0:
        print("No location points found.")
        return 0

    countries = gpd.read_file(args.countries_url).to_crs("EPSG:4326")
    states_all = gpd.read_file(args.us_states_url).to_crs("EPSG:4326")
    us_states = states_all[states_all["admin"] == "United States of America"].copy()
    tiles = load_tile_geometries(Path(args.tile_manifest)) if args.tile_manifest else load_tile_geometries(None)

    min_lat, max_lat = float(lats.min()), float(lats.max())
    min_lon, max_lon = float(lons.min()), float(lons.max())
    lat_pad = max(2.0, (max_lat - min_lat) * 0.08)
    lon_pad = max(2.0, (max_lon - min_lon) * 0.08)

    world_heatmap_out = Path(args.world_heatmap_out)
    world_points_out = Path(args.world_points_out)
    extent_out = Path(args.extent_out)

    render_world_heatmap(
        countries=countries,
        us_states=us_states,
        tiles=tiles,
        lons=lons,
        lats=lats,
        output_path=world_heatmap_out,
    )
    render_world_points(
        countries=countries,
        us_states=us_states,
        tiles=tiles,
        lons=lons,
        lats=lats,
        output_path=world_points_out,
    )
    render_extent_points(
        countries=countries,
        us_states=us_states,
        tiles=tiles,
        lons=lons,
        lats=lats,
        output_path=extent_out,
        xlim=(min_lon - lon_pad, max_lon + lon_pad),
        ylim=(min_lat - lat_pad, max_lat + lat_pad),
    )

    print(f"locations={len(frame)}")
    print(f"tiles={len(tiles)}")
    print(f"bounds=lat[{min_lat},{max_lat}] lon[{min_lon},{max_lon}]")
    print(f"world_heatmap_out={world_heatmap_out}")
    print(f"world_points_out={world_points_out}")
    print(f"extent_out={extent_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
