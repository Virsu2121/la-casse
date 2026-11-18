"""La Casse: local reader. Reads publishers' own feeds and the Are.na API live.
Nothing is written to disk; responses are kept in memory for a short time only."""
import argparse, concurrent.futures, email.utils, html, html.parser, json, os, re, threading, time
import urllib.parse, urllib.request, urllib.robotparser, webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

try:
    from PIL import Image
except ImportError:  # colours are optional: without Pillow the project page simply shows none
    Image = None

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "la-casse.html")
UA = "LaCasseReader/1.0 (personal feed reader)"
FEED_TTL = 1800
ARENA_TTL = 600
MAX_BYTES = 8_000_000
FEEDS = {
    "letterform": "https://letterformarchive.org/feed/",
    "identitydesigned": "https://identitydesigned.com/feed/",
    "logodesignlove": "https://www.logodesignlove.com/feed",
    "brandnew": "https://www.underconsideration.com/brandnew/atom.xml",
    "bpando": "https://bpando.org/feed/",
    "grapheine": "https://grapheine.com/feed/",
    "posterhouse": "https://posterhouse.org/feed/",
    "standardsmanual": "https://standardsmanual.com/blogs/news.atom",
    "artstation": "https://www.artstation.com/artwork.rss",
    "onepagelove": "https://onepagelove.com/feed",
    "dezeen": "https://www.dezeen.com/feed/",
    "archdaily": "https://www.archdaily.com/feed",
    "printmag": "https://www.printmag.com/feed/",
    "segd": "https://segd.org/feed/",
    "signs": "https://signs.org/feed/",
    "printful": "https://www.printful.com/uk/blog/feed",
    "worthpoint": "https://www.worthpoint.com/feed/",
    "mockupworld": "https://www.mockupworld.co/feed/",
    "yellowimages": "https://yellowimages.com/feed/",
    "graphicburger": "https://graphicburger.com/feed/",
    "truegrit": "https://www.truegrittexturesupply.com/blogs/news.atom",
    "moo": "https://www.moo.com/blog/feed/",
    "madparis": "https://madparis.fr/spip.php?page=backend",
    "etapes": "https://etapes.com/feed/",
    "larevue": "https://www.larevuedudesign.com/feed/",
}
# Sites whose robots.txt allows reading a page to show its published preview image, like a link preview.
PREVIEW_HOSTS = {"www.typographicposters.com", "posterhouse.org", "bpando.org",
                 "grapheine.com", "www.grapheine.com", "identitydesigned.com", "www.logodesignlove.com", "standardsmanual.com",
                 "signs.org", "madparis.fr", "www.mockupworld.co"}
PREVIEW_TTL = 21600
KNOWN_IMAGES = set()  # only images already listed by a feed or Are.na may be analysed
VIDEO_FILE = re.compile(r"\.(mp4|webm|mov)(\?|$)", re.I)
JUNK_IMAGE = re.compile(r"gravatar|avatar|emoji|wp-includes|spacer|pixel|/icons?/|favicon", re.I)
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src http: https: data:; media-src https:; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'")

ALLOWED = {"p", "a", "img", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "em", "strong", "i", "b", "br", "figure", "figcaption"}
VOID = {"img", "br"}
DROPPED = {"script", "style", "iframe", "noscript", "svg", "object", "embed", "form", "video", "audio", "template", "head"}


def safe_url(value, base):
    value = urllib.parse.urljoin(base, (value or "").strip())
    return value if urllib.parse.urlparse(value).scheme in ("http", "https") else ""


class Sanitizer(html.parser.HTMLParser):
    """Keeps a short allowlist of tags, since feed HTML is untrusted."""

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.out, self.text, self.stack, self.skip, self.images, self.videos = base, [], [], [], 0, [], []

    def handle_starttag(self, tag, attrs):
        if tag in ("video", "source"):
            a = dict(attrs)
            src = safe_url(a.get("src"), self.base)
            if src and (tag == "video" or "video" in (a.get("type") or "") or VIDEO_FILE.search(src)) and src not in self.videos:
                self.videos.append(src)
        if tag in DROPPED:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED:
            return
        a = dict(attrs)
        if tag == "a":
            href = safe_url(a.get("href"), self.base)
            self.out.append(f'<a href="{html.escape(href)}" target="_blank" rel="noopener noreferrer">' if href else "<a>")
        elif tag == "img":
            src = safe_url(a.get("data-src") or a.get("src"), self.base)
            if not src or a.get("width") in ("0", "1") or a.get("height") in ("0", "1"):
                return
            if not JUNK_IMAGE.search(src) and src not in self.images:
                self.images.append(src)
            self.out.append(f'<img src="{html.escape(src)}" alt="{html.escape(a.get("alt") or "")}" loading="lazy" referrerpolicy="no-referrer">')
        else:
            self.out.append(f"<{tag}>")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in DROPPED:
            self.skip -= 1

    def handle_endtag(self, tag):
        if tag in DROPPED:
            self.skip = max(0, self.skip - 1)
        elif not self.skip and tag in self.stack:
            while self.stack:
                top = self.stack.pop()
                self.out.append(f"</{top}>")
                if top == tag:
                    break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data))
            self.text.append(data)

    def result(self):
        self.close()
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out), re.sub(r"\s+", " ", "".join(self.text)).strip(), self.images


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(MAX_BYTES)


def allowed_by_robots(url):
    p = urllib.parse.urlparse(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.parse(fetch(f"{p.scheme}://{p.netloc}/robots.txt").decode("utf-8", "replace").splitlines())
    except Exception:
        return True
    return rp.can_fetch(UA, url)


def local(tag):
    return tag.rsplit("}", 1)[-1]


def to_iso(value):
    value = (value or "").strip()
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return ""
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()


def clean_author(value):
    value = re.sub(r"\s+", " ", value or "").strip()
    value = re.sub(r"\S+@\S+", "", value).strip(" ()")
    return value[:80]


def parse_feed(source, raw):
    items = []
    for el in ET.fromstring(raw).iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        f, link, attached, attached_videos = {}, "", [], []
        for c in el:
            name = local(c.tag)
            if name in ("enclosure", "content", "thumbnail") and c.get("url"):
                (attached_videos if "video" in c.get("type", "") or VIDEO_FILE.search(c.get("url")) else attached).append(c.get("url"))
            if name == "link":
                if c.get("href") and c.get("rel", "alternate") == "alternate":
                    link = c.get("href")
                elif c.text and c.text.strip():
                    link = c.text.strip()
            else:
                f.setdefault(name, "".join(c.itertext()))
        link = safe_url(link, "")
        if not link:
            continue
        candidates = [f.get("encoded"), f.get("content"), f.get("description"), f.get("summary")]
        body = max((c or "" for c in candidates), key=len)
        san = Sanitizer(link)
        san.feed(body)
        content, text, images = san.result()
        extra = [u for u in (safe_url(a, link) for a in attached) if u and u not in images and not JUNK_IMAGE.search(u)]
        images = extra + images
        title = Sanitizer(link)
        title.feed(f.get("title", ""))
        items.append({
            "source": source,
            "title": title.result()[1] or link,
            "author": clean_author(f.get("creator") or f.get("author") or ""),
            "link": link,
            "date": to_iso(f.get("pubDate") or f.get("published") or f.get("updated") or f.get("date")),
            "excerpt": text[:260] + ("…" if len(text) > 260 else ""),
            "image": images[0] if images else "",
            "images": images[:60],
            "videos": [v for v in dict.fromkeys([safe_url(u, link) for u in attached_videos] + san.videos) if v][:6],
            "html": content if len(text) > 300 or len(images) >= 2 else "",
        })
    return items


def read_feed(source, url):
    if not allowed_by_robots(url):
        return source, [], "refusé par robots.txt"
    try:
        return source, parse_feed(source, fetch(url)), ""
    except Exception as e:
        return source, [], type(e).__name__


class Cache:
    def __init__(self):
        self.lock, self.values = threading.Lock(), {}

    def get(self, key, ttl, build):
        with self.lock:
            hit = self.values.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
            value = build()
            self.values[key] = (time.time(), value)
            return value


feed_cache, arena_cache, arena_image_cache = Cache(), Cache(), Cache()


def build_feeds():
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda kv: read_feed(*kv), FEEDS.items()))
    items = [i for _, rows, _ in results for i in rows]
    KNOWN_IMAGES.update(u for i in items for u in i["images"])
    items.sort(key=lambda i: i["date"], reverse=True)
    return {
        "updated": datetime.now(timezone.utc).isoformat(),
        "ok": sum(1 for _, rows, _ in results if rows),
        "total": len(FEEDS),
        "failed": {s: (err or "flux vide") for s, rows, err in results if not rows},
        "items": items,
    }


def build_arena(query):
    url = "https://api.are.na/v2/search/channels?" + urllib.parse.urlencode({"q": query, "per": 18})
    data = json.loads(fetch(url))
    rows = []
    for c in data.get("channels", []):
        user, slug = (c.get("user") or {}).get("slug"), c.get("slug")
        if user and slug and c.get("status") != "private":
            rows.append({"title": c.get("title") or slug, "author": (c.get("user") or {}).get("username") or user,
                         "count": c.get("length") or 0, "link": f"https://www.are.na/{user}/{slug}"})
    return {"query": query, "items": rows}


def build_arena_images(query):
    """Public image blocks from the first few Are.na channels matching the query."""
    url = "https://api.are.na/v2/search/channels?" + urllib.parse.urlencode({"q": query, "per": 8})
    channels = [c for c in json.loads(fetch(url)).get("channels", []) if c.get("status") != "private" and (c.get("length") or 0) >= 6][:3]
    rows = []
    for c in channels:
        time.sleep(0.3)
        try:
            data = json.loads(fetch(f"https://api.are.na/v2/channels/{urllib.parse.quote(c['slug'])}/contents?per=30"))
        except Exception:
            continue
        for b in data.get("contents", []):
            src = (((b.get("image") or {}).get("display") or {}).get("url")) or ""
            if b.get("class") == "Image" and b.get("id") and safe_url(src, ""):
                KNOWN_IMAGES.add(src)
                rows.append({"image": src, "title": b.get("title") or c.get("title") or "", "channel": c.get("title") or "",
                             "author": (b.get("user") or {}).get("username") or b.get("connected_by_username") or "",
                             "link": f"https://www.are.na/block/{b['id']}"})
    return {"query": query, "items": rows}


robots_by_host, host_locks, host_last, previews = {}, {}, {}, {}
preview_guard = threading.Lock()
META_TAG = re.compile(r"<meta\b[^>]*>", re.I)


def host_rules(host):
    with preview_guard:
        rp = robots_by_host.get(host)
        lock = host_locks.setdefault(host, threading.Lock())
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.parse(fetch(f"https://{host}/robots.txt").decode("utf-8", "replace").splitlines())
        except Exception:
            rp.parse([])
        robots_by_host[host] = rp
    return rp, lock


def read_preview(url):
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https" or parts.netloc not in PREVIEW_HOSTS:
        return ""
    rp, lock = host_rules(parts.netloc)
    if not rp.can_fetch(UA, url):
        return ""
    delay = max(1.0, float(rp.crawl_delay(UA) or 0))
    with lock:  # one request at a time per site, spaced by its crawl delay
        wait = host_last.get(parts.netloc, 0) + delay - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            page = fetch(url)[:800_000].decode("utf-8", "replace")
        finally:
            host_last[parts.netloc] = time.time()
    for tag in META_TAG.findall(page):
        if re.search(r"""(?:property|name)=["'](?:og:image|twitter:image)["']""", tag, re.I):
            m = re.search(r"""content=["']([^"']+)""", tag, re.I)
            if m:
                return safe_url(html.unescape(m.group(1)), url)
    return ""


def preview_image(url):
    hit = previews.get(url)
    if hit and time.time() - hit[0] < PREVIEW_TTL:
        return hit[1]
    try:
        image = read_preview(url)
    except Exception:
        image = ""
    if len(previews) > 6000:
        previews.clear()
    previews[url] = (time.time(), image)
    return image


colour_cache = {}


def palette(url):
    """Up to five dominant colours of an image, read in memory and never saved."""
    if Image is None or not safe_url(url, ""):
        return []
    if url in colour_cache:
        return colour_cache[url]
    try:
        im = Image.open(BytesIO(fetch(url)))
        im.draft("RGB", (160, 160))
        im = im.convert("RGB")
        im.thumbnail((96, 96))
        q = im.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
        pal, counts = q.getpalette(), sorted(q.getcolors() or [], reverse=True)
        total = sum(c for c, _ in counts) or 1
        picked = []
        for count, index in counts:
            r, g, b = pal[index * 3:index * 3 + 3]
            near = next((p for p in picked if abs(p[0] - r) + abs(p[1] - g) + abs(p[2] - b) < 48), None)
            if near:
                near[3] += count
            else:
                picked.append([r, g, b, count])
        picked.sort(key=lambda p: -p[3])
        colours = [{"hex": "#%02x%02x%02x" % (r, g, b), "share": round(100 * c / total)} for r, g, b, c in picked[:5] if c / total >= 0.03]
    except Exception:
        colours = []
    if len(colour_cache) > 6000:
        colour_cache.clear()
    colour_cache[url] = colours
    return colours


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = False  # on Windows, reuse would let an old copy keep answering on the same port
    daemon_threads = True


def open_server(first_port):
    """Takes the first free port, so a copy left open can never hide this version."""
    for port in range(first_port, first_port + 20):
        try:
            return LocalServer(("127.0.0.1", port), Handler), port
        except OSError:
            continue
    raise SystemExit("Aucun port libre : ferme les autres fenêtres de La Casse, puis relance.")


class Handler(BaseHTTPRequestHandler):
    def send_body(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value, status=200):
        self.send_body(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            return self.send_json({"error": "hôte refusé"}, 403)
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/la-casse.html"):
            with open(PAGE, "rb") as f:
                return self.send_body(200, f.read(), "text/html; charset=utf-8")
        if url.path == "/api/feeds":
            return self.send_json(feed_cache.get("feeds", FEED_TTL, build_feeds))
        if url.path == "/api/arena":
            query = urllib.parse.parse_qs(url.query).get("q", [""])[0].strip()[:100]
            if not query:
                return self.send_json({"query": "", "items": []})
            try:
                return self.send_json(arena_cache.get(query.lower(), ARENA_TTL, lambda: build_arena(query)))
            except Exception as e:
                return self.send_json({"error": type(e).__name__}, 502)
        if url.path == "/api/arena-images":
            query = urllib.parse.parse_qs(url.query).get("q", [""])[0].strip()[:100]
            if not query:
                return self.send_json({"query": "", "items": []})
            try:
                return self.send_json(arena_image_cache.get(query.lower(), FEED_TTL, lambda: build_arena_images(query)))
            except Exception as e:
                return self.send_json({"error": type(e).__name__}, 502)
        if url.path == "/api/colors":
            image = urllib.parse.parse_qs(url.query).get("u", [""])[0]
            return self.send_json({"colors": palette(image) if image in KNOWN_IMAGES else []})
        if url.path == "/galerie.json" and os.path.exists(os.path.join(HERE, "galerie.json")):
            with open(os.path.join(HERE, "galerie.json"), "rb") as f:
                return self.send_body(200, f.read(), "application/json; charset=utf-8")
        if url.path == "/api/preview":
            return self.send_json({"image": preview_image(urllib.parse.parse_qs(url.query).get("u", [""])[0])})
        self.send_json({"error": "introuvable"}, 404)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    server, port = open_server(args.port)
    url = f"http://127.0.0.1:{port}/"
    print("La Casse tourne sur", url, "(Ctrl+C pour arrêter)", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    server.serve_forever()
