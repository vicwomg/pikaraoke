# Security Hardening Changes

This document outlines all security hardening improvements applied to PiKaraoke.

## Overview

These changes address CRITICAL and MEDIUM severity vulnerabilities across authentication, API protection, and system command execution. All changes maintain backward compatibility with existing deployments.

## Changes by Category

### 1. Session Token Authentication (CRITICAL)

**Files Modified:**
- `pikaraoke/lib/session_manager.py` (NEW)
- `pikaraoke/lib/current_app.py`
- `pikaraoke/routes/admin.py`

**What Changed:**
- Replaced plaintext password cookies with cryptographically secure session tokens (256-bit entropy)
- Tokens stored server-side with automatic expiration (24 hours default)
- Background cleanup thread prevents token store memory leaks
- Cookie name changed from `admin` to `admin_session`

**Threat Model:**
- Prevents password exposure in cookies or local storage (XSS, credential extraction)
- Prevents session fixation attacks (tokens are single-use server-side)
- Prevents plaintext password reuse across sessions

**Backward Compatibility:**
- Deployments with `admin_password=None` (open access) continue to work unchanged
- Admin authentication check remains transparent to route logic

### 2. API Rate Limiting (MEDIUM)

**Files Modified:**
- `pikaraoke/lib/rate_limiter.py` (NEW)
- `pikaraoke/routes/search.py`
- `pikaraoke/app.py`
- `pyproject.toml`

**What Changed:**
- Added Flask-Limiter integration with per-IP rate limiting
- Search endpoint: 10 requests per minute per IP
- Preview endpoint: 20 requests per minute per IP
- Download endpoint: 6 requests per hour per IP

**Threat Model:**
- Prevents brute force attacks on search functionality
- Prevents automated YouTube URL scraping/enumeration
- Limits denial-of-service vectors through resource-intensive download operations

**Per-IP Implementation:**
- Limiter uses `get_remote_address()` as key function
- Supports X-Forwarded-For header for reverse proxy deployments
- Memory-backed storage suitable for single-server deployments

### 3. WebSocket Event Authentication (MEDIUM)

**Files Modified:**
- `pikaraoke/routes/socket_events.py`

**What Changed:**
- Added `@require_admin` decorator for sensitive WebSocket events:
  - `mic_latency_change`: prevents unauthorized microphone latency modification
  - `mic_echo_cancel_change`: prevents unauthorized echo cancellation toggle
  - `mic_refresh`: prevents unauthorized device enumeration
  - `mic_update`: prevents unauthorized microphone configuration
- Unauthorized clients are logged and rejected immediately

**Threat Model:**
- Prevents remote attackers from manipulating audio hardware without authentication
- Prevents device enumeration attacks to discover connected microphones
- Protects audio quality settings from unauthorized changes

### 4. System Command Execution Hardening (HIGH)

**Files Modified:**
- `pikaraoke/routes/admin.py`

**What Changed:**
- Replaced `os.system()` calls with `subprocess.run()`
- Commands passed as list of arguments instead of shell strings
- Prevented unintended shell interpretation and command injection

**Commands Hardened:**
- `shutdown now` → `subprocess.run(["shutdown", "now"])`
- `reboot` → `subprocess.run(["reboot"])`
- `raspi-config --expand-rootfs` → `subprocess.run(["raspi-config", "--expand-rootfs"])`

**Threat Model:**
- Prevents shell injection via argument manipulation
- Eliminates shell metacharacter interpretation
- Reduces kernel surface area (no /bin/sh subprocess)

**Backward Compatibility:**
- Command execution behavior identical from deployment perspective
- No changes to admin panel UI or functionality

### 5. Internet Exposure Detection (INFORMATIONAL)

**Files Modified:**
- `pikaraoke/lib/security_checks.py` (NEW)
- `pikaraoke/karaoke.py`

**What Changed:**
- Added startup detection for public IP address bindings
- Logs SECURITY WARNING if public IPv4 address detected
- Recommends VPN, reverse proxy, or network access controls

**Detection Logic:**
- Private ranges: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8
- Anything else is flagged as potential internet exposure
- Runs on startup; does not block initialization

**Purpose:**
- Alerts operators to misconfigured deployments
- Prevents accidental internet exposure of LAN-only service
- Enables early detection of network configuration errors

## Testing & Verification

All changes include:
- Unit test coverage for cryptographic token generation and validation
- Rate limiter integration testing across endpoints
- WebSocket event authentication enforcement verification
- Command execution safety verification (no shell interpretation)
- IP classification accuracy for public/private detection

## Deployment Recommendations

1. **After Upgrade:**
   - Update Flask-Limiter dependency: `Flask-Limiter>=3.5.0,<4`
   - Restart PiKaraoke service
   - Existing admin passwords remain valid (new token system is transparent)

2. **For HTTPS/Reverse Proxy:**
   - Configure reverse proxy to set `X-Forwarded-For` header
   - Rate limiting respects this header for accurate per-IP tracking

3. **For High-Availability Deployments:**
   - Current implementation stores session tokens in memory
   - Multiple PiKaraoke instances do NOT share session state
   - For load-balanced deployments, consider sticky sessions or Redis-backed token store

4. **For Internet-Exposed Deployments:**
   - If startup warning appears, secure with: VPN, authentication gateway, or reverse proxy
   - Do NOT disable the warning—it indicates a configuration issue

## Security Contact

For security issues, please follow the disclosure policy in SECURITY.md.

---

**Prepared By:** DJ Council (@djkidnyce)

**Sign-off:** DJ & His Cybersecurity Department ✅
