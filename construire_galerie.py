"""Builds galerie.json for the hosted site: titles, short descriptions, links, image addresses and colours.
No article and no image file is stored; images stay hosted by the original sites."""
import concurrent.futures, json, re, sys, time
from datetime import datetime, timezone

import serveur

ARENA_TERMS = ["poster", "logo", "typography", "ephemera", "branding", "ticket", "packaging", "lanyard", "band merch",
               "wayfinding", "album cover", "mockup", "texture", "illustration", "web design"]
MIN_FEEDS = 5


def menu_terms(page="la-casse.html"):
    """Are.na queries of the Sites web menu, so its categories open instantly online."""
    html = open(page, encoding="utf-8").read()
    block = html[html.index("const WEB_MENU = ["):html.index("const WEB_GALLERIES")]
    return re.findall(r'\["[^"]+", "[^"]+", "([^"]+)"\]', block)
EXCERPT = 220


def short(text):
    if len(text) <= EXCERPT:
        return text
    return text[:EXCERPT].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def main(out_path):
    feeds = serveur.build_feeds()
    print("flux", feeds["ok"], "/", feeds["total"], feeds["failed"], flush=True)
    if feeds["ok"] < MIN_FEEDS:
        raise SystemExit("Trop peu de flux ont répondu : le site en ligne reste sur sa version précédente.")
    items = []
    for i in feeds["items"]:
        images = i["images"]
        if not images:
            image = serveur.preview_image(i["link"])
            images = [image] if image else []
        if images:
            items.append({"source": i["source"], "title": i["title"], "author": i.get("author", ""), "link": i["link"], "date": i["date"],
                          "images": images, "videos": i.get("videos", []), "image": images[0], "excerpt": short(i["excerpt"]), "html": ""})
    arena = {}
    terms = list(dict.fromkeys(ARENA_TERMS + menu_terms()))
    print("recherches Are.na", len(terms), flush=True)
    for term in terms:
        try:
            arena[term] = serveur.build_arena_images(term)["items"]
        except Exception as e:
            print("Are.na", term, type(e).__name__, flush=True)

    start = time.time()
    targets = [(i, i["images"][0]) for i in items] + [(r, r["image"]) for rows in arena.values() for r in rows]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for (owner, _), colours in zip(targets, pool.map(lambda t: serveur.palette(t[1]), targets)):
            owner["colors"] = colours
    print("couleurs", sum(1 for o, _ in targets if o["colors"]), "/", len(targets), f"{time.time() - start:.0f}s", flush=True)

    data = {"updated": datetime.now(timezone.utc).isoformat(), "items": items, "arena": arena}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("articles", len(items), "images", sum(len(i["images"]) for i in items), "Are.na", sum(len(v) for v in arena.values()), flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "galerie.json")
