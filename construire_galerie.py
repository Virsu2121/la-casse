"""Builds galerie.json for the hosted site: titles, links and image addresses only.
No article text and no image file is stored; images stay hosted by the original sites."""
import json, sys, time
from datetime import datetime, timezone

import serveur

ARENA_TERMS = ["poster", "logo", "typography", "ephemera", "branding", "ticket", "packaging", "lanyard", "band merch",
               "wayfinding", "album cover", "mockup", "texture", "illustration", "web design"]
MIN_FEEDS = 5


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
            items.append({"source": i["source"], "title": i["title"], "link": i["link"], "date": i["date"], "images": images, "image": images[0], "excerpt": "", "html": ""})
    arena = {}
    for term in ARENA_TERMS:
        try:
            arena[term] = serveur.build_arena_images(term)["items"]
        except Exception as e:
            print("Are.na", term, type(e).__name__, flush=True)
        time.sleep(1)
    data = {"updated": datetime.now(timezone.utc).isoformat(), "items": items, "arena": arena}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("articles", len(items), "images", sum(len(i["images"]) for i in items), "Are.na", sum(len(v) for v in arena.values()), flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "galerie.json")
