# Security Policy

## Supported Versions

The following versions of PiKaraoke are currently supported with security updates:

| Version | Supported          |
| ------- | ------------------ |
| 1.22.0+ | ✅ Yes             |
| 1.21.x  | ❌ End of Life     |
| < 1.21  | ❌ End of Life     |

## Reporting a Vulnerability

### Responsible Disclosure

Do not open public issues for security vulnerabilities. Instead:

1. **Email:** Send details to the maintainer via GitHub (check the repository for contact info)
2. **Include:**
   - Type of vulnerability (e.g., authentication bypass, XSS, command injection)
   - Location in codebase (file, function, line number)
   - Steps to reproduce
   - Severity assessment (CRITICAL, HIGH, MEDIUM, LOW)
   - Suggested fix (optional)

3. **Timeline:**
   - We will acknowledge receipt within 48 hours
   - We aim to provide a fix within 7 days for CRITICAL severity
   - We will coordinate a responsible disclosure timeline with you

### Public Disclosure

After a fix is merged:
- We will release a new patch version promptly
- Release notes will include security advisory details
- Credit will be given to the reporter (if desired)

## Security Best Practices for Deployment

### 1. Network Access Control

**Recommended:** Run PiKaraoke on a trusted LAN only.

- PiKaraoke is designed for local area networks (LANs)
- It is NOT intended to be internet-exposed
- The startup warning detects public IP binding and alerts you

**If Internet Access is Required:**
- Use a VPN to access the service remotely
- Run behind a reverse proxy (nginx, Caddy) with authentication
- Implement IP-based access control
- Use TLS/HTTPS exclusively (never HTTP over the internet)

### 2. Authentication

- Set a strong `admin_password` if you need to restrict access
- Default open access (`admin_password=None`) assumes a trusted LAN
- Session tokens expire after 24 hours (user must re-authenticate)
- Change admin password periodically

### 3. System Security

- Run PiKaraoke in a non-privileged user account (never as root)
- Restrict filesystem permissions on the music library directory
- Use a firewall to limit access to the PiKaraoke port (default 5555)
- Keep your OS and dependencies patched and up to date

### 4. API Rate Limiting

- Rate limiting is enabled by default
- Rate limits are per-IP address
- If behind a reverse proxy, ensure `X-Forwarded-For` header is set
- Tune rate limits in `init_rate_limiter()` if needed for your deployment

### 5. Logging and Monitoring

- Monitor logs for unauthorized access attempts
- Watch for repeated rate limit violations (potential attack)
- Check startup logs for internet exposure warnings
- Implement log rotation to prevent disk space issues

## Known Security Considerations

### 1. YouTube Downloads

PiKaraoke downloads songs from YouTube via yt-dlp. YouTube's terms of service require permission from copyright holders. Ensure your use complies with local copyright laws.

### 2. Default Admin Password

Older versions stored admin passwords in plaintext cookies. Version 1.22.0+ uses cryptographic session tokens instead.

### 3. WebSocket Security

All sensitive WebSocket events now require admin authentication. Non-admin clients cannot manipulate microphone settings or device configuration.

### 4. Reverse Proxy Setup

If running behind a reverse proxy (nginx, Apache), ensure:
- Reverse proxy is configured for security (SSL/TLS)
- `X-Forwarded-For` header is properly set
- Rate limiting is aware of the correct client IP
- Admin authentication checks work with the proxy setup

## Dependency Security

PiKaraoke uses the following security-sensitive dependencies:

- **flask-limiter:** Rate limiting (maintained)
- **yt-dlp:** YouTube downloading (actively maintained)
- **cryptography:** TLS support (maintained by Python community)

All dependencies are pinned to safe version ranges in `pyproject.toml`. Dependabot updates are reviewed before merge.

## Security Advisories

### CVE References

No known CVEs currently affect the supported version. Historical vulnerabilities in dependencies are addressed by version constraints in `pyproject.toml`.

## Testing Security Changes

If you contribute security improvements:

1. Add unit tests in `tests/` for cryptographic functions
2. Test rate limiting with concurrent clients
3. Verify authentication enforcement on protected endpoints
4. Document any new security assumptions
5. Run the full test suite: `pytest tests/`

## Contact

For security questions or to report a vulnerability, please reach out to the maintainer through the GitHub repository.

---

**Last Updated:** August 22, 2026

**Version:** 1.22.0+

**Status:** Active ✅
