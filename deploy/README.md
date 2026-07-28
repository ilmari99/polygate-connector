# Deployment: a persistent public URL

The URL stays the same across crashes, restarts, and reboots because it is
pinned by DNS + a *named* Cloudflare tunnel, while both processes restart
themselves. One-time setup (needs a domain whose nameservers point at
Cloudflare; the free plan is fine):

```bash
# 1. authorize once (opens a browser)
cloudflared tunnel login

# 2. create the named tunnel (writes ~/.cloudflared/<UUID>.json)
cloudflared tunnel create polygate-mcp

# 3. bind your hostname to it (creates the CNAME)
cloudflared tunnel route dns polygate-mcp mcp.example.com

# 4. config + run as a service (survives reboot)
cp deploy/cloudflared-config.yml ~/.cloudflared/config.yml   # fill in UUID + host
sudo cloudflared service install
```

Then run the connector so it also restarts itself - either the systemd unit
in this directory, or plainly:

```bash
docker build -t polygate-connector .
docker run -d --name polygate-connector --restart=unless-stopped \
  --read-only --tmpfs /tmp --cap-drop=ALL --security-opt=no-new-privileges \
  -p 127.0.0.1:8765:8765 -e PUBLIC_HOST=mcp.example.com \
  --log-driver=json-file --log-opt=max-size=20m --log-opt=max-file=3 \
  polygate-connector
```

The connector URL is `https://mcp.example.com/mcp` - stable forever; a crash
on either side reconnects automatically. Do NOT put Cloudflare Access in
front of it (Claude cannot complete a login wall).

## Usage monitoring for a public preview

Two sources, both inside the privacy policy's envelope:

1. **Tool-level logs** - every call logs
   `tool= duration_ms= status= bytes= returned=` (no arguments, results, or
   IPs). Aggregate with:
   `docker logs polygate-connector 2>&1 | python scripts/usage_report.py`
2. **Cloudflare analytics** (dashboard, free) - request volume, unique
   visitors, geography, status codes, and cached-vs-origin traffic at the
   edge, with no logging burden on you.

Recommended edge extras before sharing the URL publicly: one WAF rate rule
(free tier) capping requests/IP/minute, and an uptime monitor on
`https://mcp.example.com/healthz`. The 7-day log retention promise in the
privacy policy is enforced by the log rotation flags above.
