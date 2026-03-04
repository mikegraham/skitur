# 029: Launchability Audit
**Status:** OPEN  
**Priority:** P2

## Description
What keeps us from giving this to people? Identify blockers for public release.

## Initial Findings (2026-03-04)

### P0 - Blockers Before Public Launch
1. **Test suite is not green in current working state.**
   - Command run: `./.venv/bin/pytest -q`
   - Result: `25 failed, 127 passed`.
   - Failures are concentrated in scoring behavior expectations (`tests/test_score.py`) and downstream web score assertions (`tests/test_web.py`), so release behavior is not baseline-stable.
2. **No health/readiness endpoint exists in the Flask app.**
   - Current routes are `/` and `/api/analyze` only (`skitur/app.py`).
   - Launch plan ticket already calls this out as required (`tickets/030-launch-plan.md`).

### P1 - High Risk for Reliability/Operations
1. **Cold-path request latency and availability depend on live remote DEM fetches.**
   - Request analysis path triggers DEM loading in-band (`skitur/report.py` -> `load_dem_for_bounds`).
   - DEM fetch is serialized under a global lock (`skitur/terrain.py`), creating head-of-line blocking for concurrent uncached requests.
2. **No explicit production serving/runbook in repository docs.**
   - README documents local package install and static report generation only.
   - Flask module still includes dev-server startup path (`app.run(...)`) and no documented production process manager configuration.
3. **Dependency declaration risk: SciPy is used directly but not listed in core dependencies.**
   - `scipy.ndimage.map_coordinates` is imported in `skitur/terrain.py`.
   - `pyproject.toml` core `dependencies` does not list `scipy`, relying on transitive install behavior.
4. **No request-level abuse controls beyond upload size limit.**
   - `MAX_CONTENT_LENGTH` is present, but no rate limiting / concurrency controls / timeout policy are defined in app code.

### P2 - Important but Not Immediate Blockers
1. **Input hardening is asymmetric between web and CLI paths.**
   - Web path rejects `DOCTYPE/ENTITY` markers before parsing.
   - CLI path (`python -m skitur`) flows directly into GPX parse without the same pre-check.
2. **Operational observability is minimal.**
   - No health endpoint, no metrics endpoint, no explicit readiness/warmup signaling.

## Evidence Pointers
1. `skitur/app.py` (routes, upload checks, dev-server entrypoint)
2. `skitur/report.py` (`_compute_analysis` cold-path DEM load)
3. `skitur/terrain.py` (global lock + network-backed DEM stitching)
4. `pyproject.toml` (dependency set)
5. `README.md` (current setup/deploy guidance)
6. `tickets/030-launch-plan.md` (readiness endpoint requirement already documented)

## Suggested Next Pass
1. Restore green test baseline (or intentionally update expected score curves + tests in one coherent change).
2. Add `/healthz` and `/readyz` endpoints (ready should verify DEM provider reachability and optional warm cache seed).
3. Document production deployment path (recommended process manager, worker model, env vars, startup checks).
4. Make dependencies explicit (`scipy` and any other direct imports).
5. Define abuse controls: per-IP throttling and request timeout budget for `/api/analyze`.
