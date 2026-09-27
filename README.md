# Reverse proxy (nginx, HTTPS-only) for photo.laurentml.fr

A separate Docker Compose stack, independent of the photographer-site
one, that sits in front of it: terminates TLS, gets and renews a Let's
Encrypt certificate automatically, and forwards
`https://photo.laurentml.fr` to the site's container.

It's built on **nginx-proxy** + **acme-companion** rather than a
hand-written `nginx.conf` + a manual certbot cron job. Both are nginx
under the hood - nginx-proxy *is* nginx, with its config regenerated
automatically (via `docker-gen`, built into the image) whenever a
container on the same network advertises a `VIRTUAL_HOST`. That
avoids the usual failure mode of a hand-rolled setup: renewing the
cert but forgetting to signal nginx to reload it. acme-companion
handles both the renewal and the reload.

## How "HTTPS only" is enforced

- Port **443** serves the site, TLS terminated here with a
  Let's Encrypt certificate for `photo.laurentml.fr`, modern
  TLS-only policy (`SSL_POLICY=Mozilla-Intermediate`: TLS 1.2+, no
  weak ciphers).
- Port **80** is still open - it has to be, that's how Let's Encrypt
  validates domain ownership (the HTTP-01 challenge) - but it never
  serves the site itself. Once a certificate exists, nginx-proxy
  redirects every plain-HTTP request on port 80 to the `https://`
  equivalent (this is its default behaviour, nothing extra to
  configure). `vhost.d/photo.laurentml.fr` additionally sends
  `Strict-Transport-Security`, so browsers that have visited once stop
  even trying HTTP on their own.
- The photographer-site container itself is **not** reachable from the
  internet at all: its compose file now binds its port to
  `127.0.0.1` only (see the patch below) and joins this stack's
  internal `edge` network, so the only way in from outside is through
  nginx here.

## One-time setup on the host

1. **DNS**: point `photo.laurentml.fr` (an `A`/`AAAA` record) at this
   server's public IP. Let's Encrypt's HTTP-01 challenge needs this to
   already resolve correctly before the first cert request.

2. **Firewall**: make sure ports 80 and 443 are open inbound (443 is
   the one that matters for visitors; 80 only needs to be reachable by
   Let's Encrypt's validation servers and for the redirect).

3. **Shared Docker network** (created once, outside either compose
   file - both stacks reference it as `external: true`):
   ```bash
   docker network create edge
   ```

4. **Update the photographer-site stack** to join that network and
   advertise itself to nginx-proxy. This has already been applied to
   the `docker-compose.yml` you have - it now:
   - binds its port to `127.0.0.1:${HOST_PORT:-3000}` instead of all
     interfaces (still useful for local debugging over an SSH tunnel,
     not reachable from the internet)
   - joins the `edge` network
   - sets `VIRTUAL_HOST` / `VIRTUAL_PORT` / `LETSENCRYPT_HOST` env vars,
     which is all nginx-proxy and acme-companion need to pick it up
     automatically - no proxy-side config file references it by name.

   If you already have the site running from before, just pull the
   updated `docker-compose.yml` and re-apply it:
   ```bash
   cd ~/dev/photographer-site
   docker compose up -d
   ```

5. **Start the proxy stack**:
   ```bash
   cd ~/dev/reverse-proxy
   cp .env.example .env
   # edit .env: set LETSENCRYPT_EMAIL to a real address you read
   docker compose up -d
   ```

6. **Watch the first certificate get issued**:
   ```bash
   docker compose logs -f acme-companion
   ```
   Look for a line saying the certificate for `photo.laurentml.fr` was
   obtained. This only happens once DNS is correctly pointed at the
   server and ports 80/443 are reachable from the internet - if it's
   stuck, that's almost always the cause.

Once the cert is issued, `https://photo.laurentml.fr` should serve the
site, and `http://photo.laurentml.fr` should 301-redirect to it.

## Day to day

- Certificates auto-renew; nothing to run manually.
- To add another site behind this same proxy later, just give that
  site's own compose file the same three env vars
  (`VIRTUAL_HOST`/`VIRTUAL_PORT`/`LETSENCRYPT_HOST`) and put it on the
  `edge` network - no change needed here.
- `docker compose logs -f nginx-proxy` / `acme-companion` are the
  first places to look if something's not routing or a renewal fails.

## Note on testing

I validated both compose files with `docker compose config` (confirms
the YAML, variable interpolation, the shared `edge` network reference,
and the file-inside-a-named-volume mount for the HSTS snippet all
resolve correctly) but could not actually pull and run
`nginxproxy/nginx-proxy` / `nginxproxy/acme-companion` in the sandbox
this was built in - it has no route to Docker Hub. Both images are
long-established and very widely deployed for exactly this pattern, so
this is a normal, well-trodden setup, but do watch the acme-companion
logs on the first real run as described above.
