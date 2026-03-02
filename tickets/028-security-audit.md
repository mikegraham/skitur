# 028: Security Audit
**Status:** OPEN
**Priority:** P2

## Description
Perform a security audit. Make appropriate fixes. Check for: XSS in GPX filenames, path traversal, SSRF, dependency vulnerabilities, error message information leakage, etc.

---

## Audit Findings

Audit performed 2026-03-02, covering all files in `skitur/` and key dependencies.

### Finding 1: No Upload Size Limit
**Severity: High**
**File:** `skitur/web.py` (line 43)

Flask's `MAX_CONTENT_LENGTH` is never configured on the app. An attacker can upload an arbitrarily large file, consuming server disk space (temp file) and memory. A multi-gigabyte upload would fill `/tmp` and potentially crash the server or other services sharing the same disk.

**Recommended fix:** Set `app.config['MAX_CONTENT_LENGTH']` to a reasonable limit, e.g. 10 MB (`10 * 1024 * 1024`). Flask will automatically return a 413 error for oversized requests before buffering the full body.

---

### Finding 2: XML External Entity (XXE) / Billion Laughs via GPX Parsing
**Severity: High**
**File:** `skitur/gpx.py` (line 13), dependency `gpxpy==1.6.2`

GPX files are XML. The `gpxpy` library parses XML using `xml.etree.ElementTree` (or `lxml` if installed). The stdlib `xml.etree.ElementTree` module is vulnerable to:

- **Billion Laughs (XML bomb):** A crafted GPX file with exponentially expanding entity definitions can consume gigabytes of memory, crashing the server. Example: a 1 KB XML file that expands to several GB in memory.
- **External Entity Injection (XXE):** With some parsers, `<!ENTITY>` declarations can read local files (`file:///etc/passwd`) or make outbound HTTP requests. Python's stdlib `ElementTree` does not process external entities by default, so classic XXE file exfiltration is not possible. However, the billion laughs vector remains.

The `gpxpy` parser (v1.6.2) calls `mod_etree.XML(self.xml)` with no protection against entity expansion. `defusedxml` is installed as a dependency of the project but is not used by `gpxpy`.

**Recommended fix:** Either (a) wrap the GPX parsing with `defusedxml` by monkey-patching or pre-validating the XML, or (b) set a custom limit with `xml.etree.ElementTree` if using Python 3.13+ (which has built-in entity expansion limits). A simpler approach: reject any uploaded file containing `<!ENTITY` or `<!DOCTYPE` strings before passing to gpxpy.

---

### Finding 3: Debug Mode Hardcoded On
**Severity: High**
**File:** `skitur/web.py` (line 271)

```python
app.run(debug=True, port=args.port)
```

When run via `python -m skitur.web`, Flask starts with `debug=True`. In debug mode:
- The interactive Werkzeug debugger is enabled, which allows **arbitrary code execution** on the server if an attacker can trigger an exception and access the debugger PIN.
- Automatic reloading is enabled, which is a minor concern.
- Detailed tracebacks are shown to users.

This is the `__main__` block so it only applies when running directly (not via `flask run`), but it is still a dangerous default that could be used in production.

**Recommended fix:** Remove the hardcoded `debug=True` or make it conditional on an environment variable: `app.run(debug=os.environ.get('FLASK_DEBUG', '').lower() == '1', port=args.port)`.

---

### Finding 4: Exception Messages Leaked to Client
**Severity: Medium**
**File:** `skitur/web.py` (lines 50-52)

```python
except Exception as e:
    logger.exception("Analysis failed")
    return jsonify({"error": str(e)}), 500
```

The raw Python exception message is returned to the client as JSON. Depending on the exception, this can leak:
- Internal file paths (e.g., from `FileNotFoundError`)
- Database connection strings or credentials (if any were involved)
- Library internal details (numpy shapes, scipy errors)
- Stack trace fragments from chained exceptions

**Recommended fix:** Return a generic error message to the client (`"Analysis failed. Please check your GPX file."`) and keep the detailed exception in server logs only (which is already done via `logger.exception`).

---

### Finding 5: Known Vulnerable Dependencies
**Severity: Medium**
**File:** `pyproject.toml`

`pip-audit` reports 4 known vulnerabilities in 3 packages:

| Package    | Version | CVE              | Fixed In |
|------------|---------|------------------|----------|
| Flask      | 3.1.2   | CVE-2026-27205   | 3.1.3    |
| Werkzeug   | 3.1.5   | CVE-2026-27199   | 3.1.6    |
| pip        | 25.1.1  | CVE-2025-8869    | 25.3     |
| pip        | 25.1.1  | CVE-2026-1703    | 26.0     |

Dependencies are unpinned in `pyproject.toml` (e.g., just `"flask"` with no version constraint), which means builds are not reproducible and vulnerable versions can be installed.

**Recommended fix:** (a) Update Flask to >=3.1.3 and Werkzeug to >=3.1.6 immediately. (b) Pin dependency versions or use a lockfile (`pip freeze > requirements.txt` or a `uv.lock` / `pdm.lock`). (c) Update pip in the virtualenv.

---

### Finding 6: No Rate Limiting on Analysis Endpoint
**Severity: Medium**
**File:** `skitur/web.py` (line 34)

The `/api/analyze` endpoint performs CPU-intensive computation (DEM download, slope grid computation, contour extraction) that takes 10-30 seconds per request. There is no rate limiting. An attacker can submit many concurrent requests to:
- Exhaust server CPU and memory (each request holds large numpy arrays)
- Trigger excessive outbound requests to USGS 3DEP servers (potential abuse/ban)
- Fill disk with cached DEM tiles in `~/.cache/skitur/3dep/`

**Recommended fix:** Add rate limiting via `flask-limiter` (e.g., 5 requests per minute per IP). Consider also limiting concurrent processing with a semaphore or task queue.

---

### Finding 7: SSRF via DEM Bounding Box
**Severity: Medium**
**File:** `skitur/terrain.py` (lines 155-160), `skitur/web.py` (line 175)

User-uploaded GPX coordinates are passed directly to `load_dem_for_bounds()`, which computes a bounding box and passes it to `seamless_3dep.get_dem(bbox, ...)`. This triggers outbound HTTP requests to USGS servers with user-controlled lat/lon bounds.

While this is not traditional SSRF (the destination is always USGS), an attacker can:
- Force the server to download extremely large DEM regions by crafting a GPX file with points at extreme corners (e.g., spanning all of Alaska), causing large downloads and memory allocation.
- Abuse the server as a proxy to make many requests to USGS, potentially getting the server's IP banned.

The `seamless_3dep` library has a `MAX_PIXELS = 8_000_000` safeguard but the bounding box size is otherwise unconstrained.

**Recommended fix:** Validate that the GPX track bounding box is within reasonable limits (e.g., max 0.5 degrees span in lat/lon, which covers ~35 miles) before calling `load_dem_for_bounds`. Reject tracks that span unreasonably large areas.

---

### Finding 8: CDN Scripts Without Subresource Integrity (SRI)
**Severity: Medium**
**File:** `skitur/templates/index.html` (lines 7-9)

```html
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://cdn.plot.ly/plotly-3.3.1.min.js"></script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
```

Three external resources are loaded from `unpkg.com` and `cdn.plot.ly` without `integrity` attributes. If either CDN is compromised or serves a modified file (CDN supply chain attack), arbitrary JavaScript would execute in every user's browser.

**Recommended fix:** Add `integrity="sha384-..."` and `crossorigin="anonymous"` attributes to all external `<script>` and `<link>` tags. Generate the hashes from known-good copies of each file.

---

### Finding 9: No Security Headers
**Severity: Low**
**File:** `skitur/web.py`

The Flask app sets no security-related HTTP headers. Missing headers include:
- `Content-Security-Policy`: Would prevent inline script injection (though the app relies on inline `<script>` blocks, a policy could still restrict `eval`, external origins, etc.)
- `X-Content-Type-Options: nosniff`: Prevents MIME-type sniffing
- `X-Frame-Options: DENY`: Prevents clickjacking via iframe embedding
- `Strict-Transport-Security`: Forces HTTPS (relevant if deployed with TLS)

**Recommended fix:** Add security headers via Flask's `@app.after_request` or use the `flask-talisman` library. Note: adding a strict CSP would require refactoring the inline scripts into external files.

---

### Finding 10: No CSRF Protection
**Severity: Low**
**File:** `skitur/web.py` (line 34)

The `/api/analyze` endpoint accepts POST requests with no CSRF token validation. A malicious website could submit a form targeting this endpoint, causing the victim's browser to upload a file and trigger server-side processing.

The practical impact is low because: (a) the endpoint processes a file and returns JSON, so the attacker cannot read the response (same-origin policy), and (b) the only damage is wasted server resources.

**Recommended fix:** If the app will ever handle authenticated sessions or sensitive state changes, add CSRF protection via `flask-wtf`. For the current stateless API, this is low priority.

---

### Finding 11: Temp File Created with Predictable Suffix
**Severity: Low**
**File:** `skitur/web.py` (line 43)

```python
with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tmp:
```

The temp file uses `delete=False` and is properly cleaned up in a `finally` block (line 54), which is correct. However:
- If the process crashes between file creation and the `finally` block (e.g., OOM kill during DEM download), orphaned temp files accumulate in `/tmp`.
- The `.gpx` suffix is cosmetic and harmless.

The cleanup is otherwise sound. No path traversal is possible because `tempfile.NamedTemporaryFile` generates random names in the system temp directory.

**Recommended fix:** Consider a periodic cleanup of stale `*.gpx` files in `/tmp`, or use a context manager pattern that is more crash-resilient. Alternatively, use `tempfile.TemporaryDirectory()` with a timeout-based cleanup.

---

### Finding 12: Global Mutable State (DEM Cache) Not Thread-Safe
**Severity: Low**
**File:** `skitur/terrain.py` (lines 128, 139)

```python
_dem_cache: DEMCache | None = None
```

The module-level `_dem_cache` is a global variable mutated by `load_dem_for_bounds()`. If Flask is run with a multi-threaded server (e.g., `gunicorn --threads`), concurrent requests could:
- Race on the cache check (`_dem_cache.covers(...)`) and download
- Corrupt the cache if two threads write simultaneously

With Flask's default development server (single-threaded), this is not exploitable. With production WSGI servers using threads, it could cause incorrect analysis results or crashes.

**Recommended fix:** Add a `threading.Lock` around the cache check-and-update in `load_dem_for_bounds()`, or use a thread-local cache.

---

### Non-Findings (Things That Are OK)

1. **XSS via GPX filename:** The filename is rendered via `document.getElementById("tour-name").textContent = ...` which safely escapes HTML. No vulnerability.

2. **XSS via innerHTML:** The `renderScore()` and `renderStats()` functions use `.innerHTML` but all interpolated values are numeric (via `.toFixed()`) or hardcoded strings. No user-controlled strings reach innerHTML.

3. **XSS via error messages:** Error messages are rendered via `.textContent`, not `.innerHTML`. Safe.

4. **Path traversal via file upload:** The uploaded filename is never used to construct file paths. The server uses `tempfile.NamedTemporaryFile()` which generates a random safe name. No vulnerability.

5. **GPX parsing code injection:** The `gpxpy` library parses XML into a structured object; latitude/longitude/elevation values are extracted as floats. There is no code execution path from GPX content (aside from the XXE/billion laughs XML-level issue noted above).

6. **Temp file cleanup:** The `finally` block at line 54 properly deletes the temp file even when exceptions occur. This is correct.

---

## Summary by Severity

| Severity | Count | Findings |
|----------|-------|----------|
| Critical | 0     | -- |
| High     | 3     | #1 (no upload limit), #2 (XXE/XML bomb), #3 (debug mode) |
| Medium   | 4     | #4 (error leak), #5 (vulnerable deps), #6 (no rate limit), #7 (SSRF/DEM abuse), #8 (no SRI) |
| Low      | 4     | #9 (no security headers), #10 (no CSRF), #11 (temp files), #12 (thread safety) |

## Recommended Fix Priority

1. **Immediate:** Set `MAX_CONTENT_LENGTH` (Finding #1) -- one-line fix, high impact.
2. **Immediate:** Remove `debug=True` (Finding #3) -- one-line fix, prevents RCE.
3. **Immediate:** Update Flask/Werkzeug (Finding #5) -- `pip install -U flask werkzeug`.
4. **Soon:** Add XML bomb protection (Finding #2) -- reject DOCTYPE/ENTITY in uploads.
5. **Soon:** Sanitize error messages (Finding #4) -- return generic message to client.
6. **Soon:** Add bounding box size validation (Finding #7) -- reject huge track spans.
7. **Soon:** Add SRI hashes to CDN scripts (Finding #8).
8. **Later:** Add rate limiting (Finding #6).
9. **Later:** Add security headers (Finding #9).
10. **Later:** Thread-safety for DEM cache (Finding #12).
