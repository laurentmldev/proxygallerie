"""Root-domain gallery of the sites served behind nginx-proxy.

Lists every running container that advertises a VIRTUAL_HOST and a
gallery title, and renders one card per site (title, description,
thumbnail, link). Nothing here names any site: like nginx-proxy and
acme-companion, it discovers them from the Docker API, so a site shows
up as soon as its own compose file declares the gallery metadata.

Metadata is read from container labels first, then environment
variables (same names, upper-cased with "_" instead of "."):

    gallery.title        / GALLERY_TITLE        required to be listed
    gallery.description  / GALLERY_DESCRIPTION
    gallery.thumbnail    / GALLERY_THUMBNAIL    absolute URL, or a path
                                                on the site ("/thumb.png")
    gallery.order        / GALLERY_ORDER        sort key, lowest first
    gallery.url          / GALLERY_URL          overrides https://<host>

Standard library only; talks to the Docker Engine API over its unix
socket (read-only GET requests).
"""

import html
import http.client
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

DOCKER_SOCKET = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
PORT = int(os.environ.get("PORT", "8080"))
CACHE_SECONDS = float(os.environ.get("CACHE_SECONDS", "30"))
PAGE_TITLE = os.environ.get("PAGE_TITLE", "laurentml.fr")
PAGE_SUBTITLE = os.environ.get("PAGE_SUBTITLE", "")

FIELDS = ("title", "description", "thumbnail", "order", "url")


class DockerConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=5)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def docker_get(path):
    conn = DockerConnection(DOCKER_SOCKET)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(f"Docker API {path}: HTTP {resp.status}")
        return json.loads(body)
    finally:
        conn.close()


def site_from_container(info):
    """Build a site dict from a /containers/{id}/json payload, or None."""
    config = info.get("Config") or {}
    labels = config.get("Labels") or {}
    env = {}
    for item in config.get("Env") or []:
        key, _, value = item.partition("=")
        env[key] = value

    hosts = [h.strip() for h in env.get("VIRTUAL_HOST", "").split(",") if h.strip()]
    if not hosts:
        return None

    meta = {}
    for field in FIELDS:
        value = labels.get(f"gallery.{field}")
        if value is None:
            value = env.get(f"GALLERY_{field.upper()}")
        meta[field] = (value or "").strip()
    if not meta["title"]:
        return None

    host = hosts[0]
    url = meta["url"] or f"https://{host}"
    thumb = meta["thumbnail"]
    if thumb and not thumb.startswith(("http://", "https://", "//")):
        thumb = url.rstrip("/") + "/" + thumb.lstrip("/")
    try:
        order = float(meta["order"]) if meta["order"] else float("inf")
    except ValueError:
        order = float("inf")

    return {
        "host": host,
        "url": url,
        "title": meta["title"],
        "description": meta["description"],
        "thumbnail": thumb,
        "order": order,
    }


def discover_sites():
    sites = {}
    for summary in docker_get("/containers/json"):
        try:
            info = docker_get(f"/containers/{quote(summary['Id'])}/json")
        except Exception:
            continue  # container vanished between list and inspect
        site = site_from_container(info)
        # Several replicas of one site collapse into one card.
        if site and site["host"] not in sites:
            sites[site["host"]] = site
    return sorted(sites.values(), key=lambda s: (s["order"], s["title"].lower()))


class SiteCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._sites = []
        self._fetched = 0.0
        self.error = None

    def get(self):
        with self._lock:
            if time.monotonic() - self._fetched > CACHE_SECONDS:
                try:
                    self._sites = discover_sites()
                    self.error = None
                except Exception as exc:  # keep serving the last good list
                    self.error = str(exc)
                self._fetched = time.monotonic()
            return self._sites


CACHE = SiteCache()

STYLE = """
:root{--bg:#f6f5f2;--card:#fff;--fg:#1c1c1e;--muted:#6b6b70;--line:#e3e1dc;--accent:#2f5d8a}
@media (prefers-color-scheme:dark){:root{--bg:#131316;--card:#1d1d21;--fg:#ececef;--muted:#9a9aa2;--line:#2c2c32;--accent:#8ab4e0}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{max-width:1100px;margin:0 auto;padding:56px 20px 24px}
h1{margin:0;font-size:2rem;letter-spacing:-.02em}
header p{margin:.4rem 0 0;color:var(--muted)}
main{max-width:1100px;margin:0 auto;padding:0 20px 64px;display:grid;gap:20px;grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}
a.card{display:flex;flex-direction:column;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;color:inherit;text-decoration:none;transition:transform .15s,box-shadow .15s}
a.card:hover,a.card:focus-visible{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.12)}
.thumb{aspect-ratio:16/9;background:var(--line);display:grid;place-items:center;font-size:3rem;font-weight:700;color:var(--muted)}
.thumb img{width:100%;height:100%;object-fit:cover;display:block}
.body{padding:14px 16px 18px}
h2{margin:0;font-size:1.1rem}
.body p{margin:.35rem 0 0;color:var(--muted);font-size:.95rem}
.host{margin-top:.6rem;font-size:.8rem;color:var(--accent)}
.empty{grid-column:1/-1;color:var(--muted)}
"""


def render_page(sites):
    esc = html.escape
    cards = []
    for s in sites:
        if s["thumbnail"]:
            thumb = f'<img src="{esc(s["thumbnail"])}" alt="" loading="lazy">'
        else:
            thumb = esc(s["title"][:1].upper())
        desc = f"<p>{esc(s['description'])}</p>" if s["description"] else ""
        cards.append(
            f'<a class="card" href="{esc(s["url"])}">'
            f'<div class="thumb">{thumb}</div>'
            f'<div class="body"><h2>{esc(s["title"])}</h2>{desc}'
            f'<div class="host">{esc(s["host"])}</div></div></a>'
        )
    if not cards:
        cards.append('<p class="empty">No sites published yet.</p>')
    subtitle = f"<p>{esc(PAGE_SUBTITLE)}</p>" if PAGE_SUBTITLE else ""
    return (
        "<!doctype html><html lang=\"fr\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{esc(PAGE_TITLE)}</title><style>{STYLE}</style></head><body>"
        f"<header><h1>{esc(PAGE_TITLE)}</h1>{subtitle}</header>"
        f"<main>{''.join(cards)}</main></body></html>"
    )


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            CACHE.get()
            status = 200 if CACHE.error is None else 503
            self._send(status, CACHE.error or "ok", "text/plain; charset=utf-8")
        elif path == "/sites.json":
            sites = [{k: v for k, v in s.items() if k != "order"} for s in CACHE.get()]
            self._send(200, json.dumps(sites, ensure_ascii=False), "application/json")
        elif path == "/":
            self._send(200, render_page(CACHE.get()), "text/html; charset=utf-8")
        else:
            self._send(404, "Not found", "text/plain; charset=utf-8")

    do_HEAD = do_GET

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    print(f"gallery listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
