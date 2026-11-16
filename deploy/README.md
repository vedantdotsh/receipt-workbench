# Public demo on one VPS

Use a Linux VPS with Docker Engine and the Compose plugin, Git, a domain you
control, and at least 2 vCPUs and 4 GiB RAM. Both amd64 and arm64 are intended
targets; the image uses CPython 3.13 on Debian Bookworm, not Alpine/musl.
The locked saxonche 13.1.1 and pypdfium2 5.15.2 dependencies have wheels for both.
Use test invoices only. This is a single-server demo, not a hardened document
processing service.

Point the domain's DNS A record at the VPS, and its AAAA record only if IPv6
also reaches it. Allow inbound TCP 80 and 443; keep the app port closed.
Restrict administrative access to your management network. Docker-published
ports can bypass host firewall rules, so check the actual external exposure.
No database, cloud account, or external processing service is needed. Caddy
needs outbound DNS and HTTPS for certificate issuance and renewal.

From the repository root on the VPS:

```sh
cp deploy/.env.example deploy/.env
# Set DOMAIN to a bare hostname and ACME_EMAIL to your certificate contact.
docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --build --wait
docker compose --env-file deploy/.env -f deploy/compose.yaml logs --tail=100 -f
```

Open the domain over HTTPS. Caddy obtains and renews the certificate and
redirects HTTP to HTTPS. Its named volumes retain certificates across updates;
do not delete them with `down -v` during routine maintenance.

## Without open ports: Cloudflare Tunnel

`compose.tunnel.yaml` replaces Caddy with `cloudflared`. The server publishes
no port and its address stays out of DNS; Cloudflare terminates HTTPS and the
tunnel dials out. Use it when the domain is on Cloudflare or the server also
runs private services.

On a trusted machine, not the server, create the tunnel and its DNS record:

```sh
cloudflared tunnel login
cloudflared tunnel create receipt-workbench
cloudflared tunnel route dns receipt-workbench demo.example.com
cloudflared tunnel token receipt-workbench
```

Put only that token in `deploy/.env` on the server as `TUNNEL_TOKEN`, keep the
file at mode 600, and use `-f deploy/compose.tunnel.yaml` in every command
here. The account certificate from `tunnel login` never needs to reach the server.
Cloudflare appends the visitor address to `X-Forwarded-For`, which is the entry
the app reads. The app sends its own security headers in public mode.

## Server settings

The CLI's `--public` option or `RW_PUBLIC=1` enables public-mode limits,
separate visitor inboxes and security headers. It does not change the bind
address or configure HTTPS. Settings use environment variables; byte limits
are decimal integers, not unit strings. Restart the process after changes.

| Variable | Local default | Public default |
|---|---|---|
| `RW_MAX_FILE_BYTES` | 15728640 (15 MiB) | 5242880 (5 MiB) |
| `RW_MAX_FILES` | 25 per upload | 5 per upload |
| `RW_MAX_ITEMS` | 200 per visitor | 20 per visitor |
| `RW_MAX_REQUEST_BYTES` | 394264576 (376 MiB) | 20971520 (20 MiB) |
| `RW_MAX_VISITORS` | 1000 | 100 |
| `RW_MAX_MEMORY_BYTES` | 1073741824 (1 GiB) | 134217728 (128 MiB) |
| `RW_IDLE_SECONDS` | 0 (off) | 1800 (30 minutes) |
| `RW_VISITOR_RATE` | 0 (off) | 10 checks per window |
| `RW_IP_RATE` | 0 (off) | 30 checks per window |
| `RW_RATE_WINDOW_SECONDS` | 60 | 60 |
| `RW_MAX_CONCURRENT_CHECKS` | 1 | 1 |
| `RW_TRUST_PROXY` | false | false |

The Compose configurations enable public mode and proxy trust, fix concurrency
at one, and expose the overrides listed in `deploy/.env.example`. Only enable
proxy trust behind a trusted proxy that cannot be bypassed. The app uses the
last `X-Forwarded-For` address and `X-Forwarded-Proto` from that proxy; otherwise
it ignores forwarded headers. Cookies are HttpOnly and SameSite=Lax, and Secure
over HTTPS.

The file limit cannot exceed the 15 MiB intake ceiling; XML is limited to 5 MiB.
Excess uploads return 413, full inboxes and rate limits return 429, and unavailable
check slots or memory return 503. Checks are not queued. Both checking endpoints
share the rate window; temporary capacity refusals include `Retry-After`.
The oldest idle visitor is evicted first when visitor or memory capacity is
needed. Idle expiry runs on inbox requests and a periodic sweep, at intervals
of at most 60 seconds. Closing the browser does not immediately erase its inbox.

The upload notice is served by `GET /api/harness` from the actual mode and idle
timeout. The application also reads `RW_HOSTING_NOTICE_EN` and
`RW_HOSTING_NOTICE_DE` for custom notices; pass these into the app container if
needed. `RW_MAX_MEMORY_BYTES` covers retained PDFs, results, page images, review
events and limiter state, not temporary buffers or native-library allocations.

## Maintenance

Update, inspect, and stop from the same repository root:

```sh
git pull --ff-only
docker compose --env-file deploy/.env -f deploy/compose.yaml build --pull app
docker compose --env-file deploy/.env -f deploy/compose.yaml pull caddy
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --wait
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
docker compose --env-file deploy/.env -f deploy/compose.yaml stop
# Remove the stopped containers and networks, retaining certificates:
docker compose --env-file deploy/.env -f deploy/compose.yaml down
```

The app runs as UID/GID 10001 with a read-only root filesystem, no Linux
capabilities, and no privilege escalation. Writable application scratch uses a
64 MiB `/tmp` tmpfs. Accepted multipart uploads stay in memory; the parser's
spooling threshold equals the enforced request limit.
The final image contains the installed package and production dependencies,
not uv, compilers, source checkout, or development dependencies. Python avoids
writing bytecode. The health check makes a HEAD request to
`/static/index.html`: unlike `/`, static requests create no visitor or cookie,
even if the visitor table is full. It checks HTTP liveness, not validation.

Run exactly one app container with one worker. Do not scale it, enable reload,
or increase `WEB_CONCURRENCY`: all inboxes and rate windows live in that one
process. Restarting or replacing it loses every invoice, page image, review
decision, visitor session, and rate window by design. There is no backup of
inboxes and no zero-downtime multi-replica update.

The app has a 1536 MiB hard memory limit, no additional swap allowance, one CPU,
and 128 PIDs. Caddy has 256 MiB, no additional swap allowance, half a CPU,
128 PIDs, and a 16 MiB scratch tmpfs; its certificate/config volumes are writable.
Caddy keeps only NET_BIND_SERVICE, required by its binary and HTTPS listener.
The app retains at most 128 MiB of inbox and limiter data by default. Parsing,
native Saxon/PDFium allocations, rendering, and tmpfs consume additional memory;
the container limit bounds their combined impact, not their individual cost.
The suggested VPS leaves room for Docker, the OS, certificates, and spikes.
Keep one concurrent check and leave these headrooms when changing limits.
An out-of-memory kill restarts the demo and discards all inboxes. Docker marks
failed health checks unhealthy but does not automatically restart a live,
unhealthy process; investigate logs and restart it if necessary.

`RW_*` overrides in `.env.example` use the units and meanings listed above.
`RW_MAX_REQUEST_BYTES` defaults to 20971520 (20 MiB), including multipart framing,
and is passed to both Caddy and the app. `/api/check` also enforces the 5 MiB
per-file ceiling. The app is on an internal Docker network without a published
port or internet egress. Caddy alone can reach it from the public side.
Caddy ignores incoming forwarded headers by default; the configuration also
explicitly replaces `X-Forwarded-For` with its socket peer and
`X-Forwarded-Proto` with its request scheme, and drops `Forwarded`. Do not add
another proxy/CDN or publish the app port without revisiting this trust boundary.
Caddy adds security headers but leaves the app's CSP alone.

Confirm public mode with:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml exec app python -c 'from receipt_workbench.review.limits import Settings; settings = Settings.from_env(); print(settings); assert settings.public and settings.trust_proxy and settings.max_concurrent_checks == 1'
```

Also check that HTTPS responses include the app's Content-Security-Policy and
an HttpOnly, Secure, SameSite=Lax session cookie, and that a private browser
window cannot see another window's inbox. Do not log upload bodies or cookies.
Watch logs and host resource usage; byte limits do not prevent expensive PDFs,
native-library vulnerabilities, denial of service, or loss of availability.
