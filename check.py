import json
import os
import re
import sys
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://egy.voxcinemas.com"
# "Now showing" = bookable movies. Add "/movies/comingsoon" to also get announcements.
PAGES = [BASE + "/movies/whatson"]

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
FILE = "seen.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

IGNORE_SLUGS = {"", "whatson", "comingsoon", "movies", "advance-booking"}


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": "false"},
        timeout=20,
    )
    if not r.ok:
        print("Telegram error:", r.status_code, r.text)


def fetch_movies():
    movies = {}
    for page in PAGES:
        r = requests.get(page, timeout=20, headers=HEADERS)
        print(page, "->", r.status_code)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select('a[href*="/movies/"]'):
            href = a.get("href", "").split("?")[0].split("#")[0]
            if not re.search(r"/movies/[^/]+/?$", href):
                continue
            slug = href.rstrip("/").split("/")[-1]
            if slug in IGNORE_SLUGS:
                continue
            url = urljoin(BASE, href)
            img = a.find("img")
            alt = (img.get("alt") or "") if img else ""
            if alt.startswith("Movie poster for "):
                movies[url] = alt[len("Movie poster for "):]
            elif url not in movies:
                movies[url] = slug.replace("-", " ").title()
    return movies


def load_seen():
    try:
        with open(FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def main():
    try:
        movies = fetch_movies()
    except Exception as e:
        print("Fetch failed:", e)
        sys.exit(1)

    if not movies:
        print("No movies found - page layout may have changed or the site blocked us.")
        sys.exit(1)

    seen = load_seen()
    first_run = len(seen) == 0
    new = {u: n for u, n in movies.items() if u not in seen}

    if first_run:
        send(f"✅ Cinema bot is running. Tracking {len(movies)} movies on VOX Egypt. "
             "You'll be notified when a new one appears.")
    else:
        for url, name in new.items():
            send(f"🎬 New movie on VOX Egypt: {name}\n{url}")

    print(f"Found {len(movies)} movies, {len(new)} new.")
    with open(FILE, "w") as f:
        json.dump(sorted(seen | set(movies)), f, indent=2)


if __name__ == "__main__":
    main()
