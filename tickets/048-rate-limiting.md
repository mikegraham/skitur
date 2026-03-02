# 048: Rate Limiting on Analysis Endpoint
**Status:** OPEN
**Priority:** P2

## Description
The `/api/analyze` endpoint is CPU-intensive (10-30s per request, large numpy arrays in memory). No rate limiting exists. Concurrent requests can exhaust server resources and abuse upstream USGS 3DEP servers.

## Considerations
- Simple per-IP rate limiting (e.g., `flask-limiter` with 5 req/min) handles casual abuse
- But doesn't address concurrent resource exhaustion (5 simultaneous requests each using 500MB+ RAM)
- A processing semaphore (e.g., `threading.Semaphore(2)`) would cap concurrent analyses
- Could also use a task queue (Celery, RQ) for proper job management, but that's heavy infrastructure
- For low-user-count deployment, a semaphore + basic rate limit is probably sufficient

## Options
1. **flask-limiter** only: simple, handles repeat abuse, doesn't cap concurrency
2. **Semaphore** only: caps concurrent work, doesn't prevent rapid sequential requests
3. **Both**: most robust for a simple deployment
4. **Task queue**: overkill unless scaling to many users
