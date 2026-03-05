import re
from pathlib import Path

import pytest

from skitur import score


README_PATH = Path(__file__).resolve().parents[1] / "README.md"


def _readme_text() -> str:
    return README_PATH.read_text(encoding="utf-8")


def _extract(pattern: str, text: str) -> float:
    m = re.search(pattern, text)
    assert m, f"Pattern not found in README: {pattern}"
    return float(m.group(1))


def test_readme_rating_constants_match_score_module():
    text = _readme_text()

    assert re.search(
        rf"{score.STANDING_AVY_MIN_DEG:g}-{score.STANDING_AVY_MAX_DEG:g} degree ground",
        text,
    )
    assert re.search(rf"{score.AVY_TRACE_STEPS} steps", text)
    assert re.search(rf"{score.AVY_TRACE_MAX_M:g} m", text)
    assert re.search(rf"{score.AVY_SLOPE_MIN:g}-{score.STANDING_AVY_MIN_DEG:g}", text)
    assert re.search(rf"{score.STANDING_AVY_MAX_DEG:g}-{score.AVY_SLOPE_MAX:g}", text)


def test_readme_rating_weights_match_score_module():
    text = _readme_text()

    downhill_pct = _extract(r"- ([0-9.]+)% downhill quality", text)
    uphill_pct = _extract(r"- ([0-9.]+)% uphill quality", text)
    avy_pct = _extract(r"- ([0-9.]+)% avalanche exposure rating metric", text)

    assert downhill_pct == pytest.approx(score.DOWNHILL_WEIGHT * 100)
    assert uphill_pct == pytest.approx(score.UPHILL_WEIGHT * 100)
    assert avy_pct == pytest.approx(score.AVY_WEIGHT * 100)
    assert downhill_pct + uphill_pct + avy_pct == pytest.approx(100.0)


def test_readme_references_rating_curve_asset():
    text = _readme_text()
    required = [
        "docs/rating_curve_downhill_fun.svg",
        "docs/rating_curve_uphill_doability.svg",
        "docs/rating_curve_ground_slope_penalty.svg",
        "docs/rating_curve_ground_steepness_multiplier.svg",
    ]
    for rel_path in required:
        assert rel_path in text
        assert (README_PATH.parent / rel_path).exists()
