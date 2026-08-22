# PiKaraoke Deployment Guide

This guide covers security-hardened deployment scenarios for PiKaraoke 1.22.0+.

## Quick Start (LAN-Only)

The simplest and recommended deployment: PiKaraoke on a trusted local area network.

### Setup

```bash
# Install PiKaraoke
pip install pikaraoke

# Start the service (default: open access, no password)
pikaraoke --host 192.168.1.5 --port 5555

# Access from browser on the same LAN
# http://192.168.1.5:5555
```

### Security Notes

- Open access (`admin_password=None`) assumes a trusted LAN
- No internet exposure detection warning (good)
- All users can manage songs and settings
- Suitable for home networks, internal corporate networks, parties

## HTTPS with Nginx (Reverse Proxy)

Secure deployment for partial internet access or HTTPS requirement.

### Prerequisites

- Nginx installed and running
- Valid TLS certificate (Let's Encrypt recommended)
- Domain name (optional but recommended)

### Setup

**1. Start PiKaraoke on localhost only:**

```bash
pikaraoke --host 127.0.0.1 --port 5000 --admin-password "YourSecurePassword123"
```

**2. Configure Nginx:**

```nginx
upstream pikaraoke {
    server 127.0.0.1:5000;
}

server {
    listen 443 ssl http2;
    server_name karaoke.example.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;

    location / {
        proxy_pass http://pikaraoke;
        proxy_http_version 1.1;
        
        # WebSocket support
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Forwarded headers for rate limiting
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Host $host;
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name karaoke.example.com;
    return 301 https://$server_name$request_uri;
}
```

**3. Restart Nginx:**

```bash
sudo systemctl restart nginx
```

### Security Notes

- TLS encrypts all traffic
- `X-Forwarded-For` header ensures rate limiting uses correct client IP
- Admin password required for all protected operations
- Reverse proxy blocks direct PiKaraoke access
- Security headers prevent common attacks (XSS, clickjacking, etc.)

## HTTPS with Caddy (Simple TLS)

Caddy automatically manages TLS certificates with Let's Encrypt.

### Prerequisites

- Caddy installed
- Domain name pointing to your server

### Setup

**1. Start PiKaraoke on localhost:**

```bash
pikaraoke --host 127.0.0.1 --port 5000 --admin-password "YourSecurePassword123"
```

**2. Create Caddyfile:**

```caddyfile
karaoke.example.com {
    reverse_proxy 127.0.0.1:5000 {
        header_up X-Forwarded-For {http.request.remote}
        header_up X-Forwarded-Proto {http.request.scheme}
    }
}
```

**3. Run Caddy:**

```bash
caddy start
```

Caddy automatically obtains and renews TLS certificates.

### Security Notes

- Zero-configuration TLS (Caddy handles everything)
- Automatic certificate renewal
- Simple configuration
- Good for rapid secure deployments

## Docker Deployment

Run PiKaraoke in a container with predefined security settings.

### Dockerfile

```dockerfile
FROM python:3.10-slim

WORKDIR /app

RUN pip install pikaraoke

RUN useradd -m -u 1000 pikaraoke
USER pikaraoke

EXPOSE 5555

ENTRYPOINT ["pikaraoke"]
CMD ["--host", "0.0.0.0", "--port", "5555", "--admin-password", "pikaraoke"]
```

### docker-compose.yml

```yaml
version: '3.8'

services:
  pikaraoke:
    build: .
    ports:
      - "5555:5555"
    volumes:
      - ./songs:/root/.pikaraoke/songs
      - ./config:/root/.pikaraoke/config
    environment:
      - PIKARAOKE_ADMIN_PASSWORD=YourSecurePassword123
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    read_only_root_filesystem: false  # Needs write access for temp files

  nginx:
    image: nginx:latest
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro
    depends_on:
      - pikaraoke
    restart: unless-stopped
```

### Run

```bash
docker-compose up -d
```

## VPN Deployment (Remote Access)

Secure remote access without internet exposure.

### Setup with WireGuard

**1. Configure WireGuard on the PiKaraoke server:**

```bash
# Install WireGuard
sudo apt-get install wireguard wireguard-tools

# Generate keys
wg genkey | tee privatekey | wg pubkey > publickey

# Configure /etc/wireguard/wg0.conf
[Interface]
PrivateKey = <server-privatekey>
Address = 10.0.0.1/24
ListenPort = 51820

[Peer]
PublicKey = <client-publickey>
AllowedIPs = 10.0.0.2/32
```

**2. Start WireGuard:**

```bash
sudo wg-quick up wg0
```

**3. Start PiKaraoke (accessible only via VPN):**

```bash
pikaraoke --host 10.0.0.1 --port 5555 --admin-password "secure"
```

**4. Connect client to VPN:**

Access PiKaraoke via `http://10.0.0.1:5555` after connecting to WireGuard.

### Security Notes

- All traffic encrypted via VPN tunnel
- No internet exposure
- Scalable for multiple remote clients
- Works with mobile clients (WireGuard app)

## Rate Limiting Configuration

### Adjusting Limits

In `pikaraoke/lib/rate_limiter.py`, modify limits:

```python
def init_rate_limiter(app):
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["200 per day", "50 per hour"],
        storage_uri="memory://",
    )
    return limiter
```

### Per-Endpoint Limits

In `pikaraoke/routes/search.py`:

```python
@apply_rate_limit("10 per minute")  # Change this
def search():
    ...
```

Limits are:
- `10 per minute`: Search endpoint (prevents brute force)
- `20 per minute`: Preview endpoint (more generous, audio preview)
- `6 per hour`: Download endpoint (prevents abuse of bandwidth)

### Redis-Backed Limits (Distributed)

For multiple PiKaraoke instances:

```python
storage_uri="redis://localhost:6379",  # Instead of "memory://"
```

Requires Redis installation and Flask-Limiter[redis] extra.

## Monitoring and Logging

### Check Logs

```bash
# If running via systemd
journalctl -u pikaraoke -f

# If running directly
# Logs appear in stdout
```

### Look For

- ⚠️ `SECURITY WARNING: PiKaraoke appears to be internet exposed` — Fix network config immediately
- ℹ️ `Unauthorized access attempt to` — Monitor for attacks
- ⚠️ `Rate limit exceeded` — Check if legitimate traffic or attack

### Set Up Log Aggregation

For production deployments, collect logs centrally:

```bash
# Example: syslog
pikaraoke 2>&1 | logger -t pikaraoke

# Or: journald
systemctl start pikaraoke
journalctl -u pikaraoke -o json-pretty
```

## Security Checklist

Before deploying PiKaraoke to production:

- [ ] Network: Run on trusted LAN only OR behind VPN/reverse proxy
- [ ] Authentication: Set strong `admin_password` if needed
- [ ] Encryption: Use HTTPS/TLS for any internet-accessible deployment
- [ ] Firewall: Restrict port 5555 (or your configured port) to authorized IPs
- [ ] OS: Run PiKaraoke as non-privileged user (never root)
- [ ] Logging: Monitor startup logs for security warnings
- [ ] Updates: Keep Flask-Limiter and yt-dlp updated
- [ ] Permissions: Restrict music library directory to PiKaraoke user
- [ ] Testing: Test rate limiting is working (curl endpoint 11+ times in 1 minute)
- [ ] Backups: Back up music library regularly

## Troubleshooting

### "PiKaraoke appears to be internet exposed"

This means you're binding to a public IP. Fix by:

1. Bind to localhost: `--host 127.0.0.1`
2. Proxy via reverse proxy (Nginx/Caddy)
3. Use VPN for remote access
4. Check your firewall/network configuration

### Rate Limiting Not Working

Check:

1. Flask-Limiter installed: `pip show Flask-Limiter`
2. Extension initialized in app.py
3. Decorator applied to routes
4. IP address is different (rate limits per IP)

### WebSocket Connection Drops

Common causes:

1. Proxy timeout too short (increase in proxy config)
2. Network instability (check connectivity)
3. Admin token expired (re-authenticate)
4. Reverse proxy not forwarding WebSocket (see Nginx config above)

## Getting Help

- **Issues:** https://github.com/vicwomg/pikaraoke/issues
- **Security:** See SECURITY.md for vulnerability reporting
- **Config Help:** Check command-line args: `pikaraoke --help`

---

**Last Updated:** August 22, 2026

**Version:** 1.22.0+
