# 030: Launch Plan
**Status:** OPEN  
**Priority:** P2

## Description
What's the easiest way to deploy this with a ceiling on cost? Would it be a good idea to port the whole thing to browser-side JS?

## Health Check Endpoint

Need a health/readiness indicator so that during deployment we can:
- Spin up a new instance
- Wait for it to report healthy (ready to serve requests)
- Then flip traffic to it (blue-green / rolling deploy)

Without this, the first user request after deploy hits a cold instance and waits
~3-6s for the DEM download. A readiness probe could optionally pre-warm the DEM
cache for a known location, or at minimum confirm the service can reach S3.

## Questions for User
- Any budget ceiling in mind for hosting?
- Target user count?
