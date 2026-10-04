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

## Root domain gallery (laurentml.fr)

The `gallery` service in this stack serves the root domain
(`ROOT_DOMAIN`, default `laurentml.fr`) with a page listing every site
behind the proxy: a card per site with its title, description,
thumbnail and a link to it.

Nothing on the proxy side names those sites. Just like nginx-proxy
reads `VIRTUAL_HOST` and acme-companion reads `LETSENCRYPT_HOST`, the
gallery reads the Docker API (read-only) and lists every running
container that has a `VIRTUAL_HOST` **and** a gallery title. Each site
declares its own card in its own `docker-compose.yml`, as labels:

```yaml
services:
  web:
    # ...
    environment:
      VIRTUAL_HOST: photo.laurentml.fr
      VIRTUAL_PORT: "3000"
      LETSENCRYPT_HOST: photo.laurentml.fr
    labels:
      gallery.title: "Photo"
      gallery.description: "Portfolio of Laurent's photographs"
      gallery.thumbnail: "/gallery-thumb.jpg"   # path on the site, or a full URL
      gallery.order: "1"                        # optional, lowest first
```

The same keys also work as environment variables (`GALLERY_TITLE`,
`GALLERY_DESCRIPTION`, `GALLERY_THUMBNAIL`, `GALLERY_ORDER`, plus
`GALLERY_URL` to override the default `https://<first VIRTUAL_HOST>`);
labels win when both are set. A container without a title is simply
not listed, and a site without a thumbnail gets a letter tile.

The list refreshes every 30 seconds, so a site appears or disappears
shortly after its container starts or stops. `https://laurentml.fr/sites.json`
returns the same data as JSON.

Setup: point the root domain's `A`/`AAAA` record at this server, then
`docker compose up -d --build`. Make sure no other container already
uses `VIRTUAL_HOST=laurentml.fr`, or nginx-proxy will split traffic
between the two.

## Maintenance page

When a site behind the proxy isn't answering, visitors get a "Site en
maintenance / back soon" page (`maintenance/html/index.html`, French
and English, auto-refreshes every minute) with an HTTP 503 instead of a
bare nginx error or a browser TLS error. Two cases are covered:

- **The container is running but not answering** (crashed app,
  restarting, still booting, timing out): nginx's own 502/503/504 for
  that host is replaced by the page. This comes from the
  `include /etc/nginx/snippets/maintenance.conf;` line at the end of
  each `vhost.d/<host>` file. Error pages the app itself returns are
  left alone.
- **The container is stopped**: nginx-proxy then drops the host from
  its config entirely. `conf.d/maintenance-fallback.conf` catches such
  hostnames (it only ever answers for names that have no server block
  of their own) and serves the page over HTTPS with the certificate
  acme-companion issued earlier for that site.

For that second case nginx needs two things it can't get from
acme-companion alone, both handled by the small `maintenance-certs`
service (`maintenance/link-certs.sh`, checks every minute):

- **Where the certificate is.** acme-companion stores a certificate in
  a folder named after its *first* host (`LETSENCRYPT_HOST=a.fr,b.fr`
  goes in `certs/a.fr/`) and deletes its per-host links when the
  container stops. `maintenance-certs` keeps a permanent link per host
  in `certs/maintenance/<host>`, recorded while the site is running.
- **Reading the private key** at request time, as nginx's unprivileged
  `nginx` user. acme-companion is set to make keys readable by that
  group (`FILES_GID: "101"`, `FILES_PERMS: "640"`), and
  `maintenance-certs` applies the same to certificates acme-companion
  isn't touching (stopped sites).

A host that is the 2nd/3rd name of a certificate and whose container
was already stopped before `maintenance-certs` first ran only gets the
page over HTTPS after its container has run once.

When adding a new site, add the same `include` line to its
`vhost.d/<host>` file.

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
