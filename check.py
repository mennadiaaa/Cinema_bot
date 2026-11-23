import json
import os
import re
import sys
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Branch pages. Each lists "Now Showing" + "Advanced Booking" movies.
BRANCHES = {
    "Cairo Festival City": "https://cfc.scenecinemas.com/home",
    "District 5": "https://district5.scenecinemas.com/home",
}
# To also get "Coming Soon" announcements, add e.g.:
# "CFC Coming Soon": "https://cfc.scenecinemas.com/next_releasing",

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
FILE = "seen.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
        timeout=20,
    )
    if not r.ok:
        print("Telegram error:", r.status_code, r.text)


def fetch_branch(label, page):
    r = None
    for attempt in range(3):
        try:
            r = requests.get(page, timeout=(10, 25), headers=HEADERS)
            break
        except requests.RequestException as e:
            print(f"[{label}] attempt {attempt + 1} failed: {e}")
    if r is None:
        raise RuntimeError(f"Could not reach {page}")
    print(label, "->", r.status_code)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    movies = {}
    for a in soup.select('a[href*="/movie-details/"]'):
        href = a.get("href", "").split("?")[0].split("#")[0]
        if not re.search(r"/movie-details/[^/]+\.html$", href):
            continue
        url = urljoin(page, href)
        slug = href.rstrip("/").split("/")[-1].replace(".html", "")
        title = a.get("title") or ""
        if title:
            movies[url] = title
        elif url not in movies:
            movies[url] = slug.replace("-", " ").replace("_", " ").title()
    return movies


def load_seen():
    try:
        with open(FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def main():
    all_movies = {}  # url -> (name, branch)
    errors = 0
    for label, page in BRANCHES.items():
        try:
            for url, name in fetch_branch(label, page).items():
                all_movies[url] = (name, label)
        except Exception as e:
            errors += 1
            print(f"[{label}] failed: {e}")

    if not all_movies:
        print("No movies found from any branch.")
        sys.exit(1)

    seen = load_seen()
    first_run = len(seen) == 0
    new = {u: v for u, v in all_movies.items() if u not in seen}

    if first_run:
        send(f"✅ Cinema bot is running. Tracking {len(all_movies)} listings on Scene Cinemas. "
             "You'll be notified when a new movie appears.")
    else:
        for url, (name, branch) in new.items():
            send(f"🎬 New at Scene Cinemas ({branch}): {name}\n{url}")

    print(f"Found {len(all_movies)} listings, {len(new)} new.")
    with open(FILE, "w") as f:
        json.dump(sorted(seen | set(all_movies)), f, indent=2)


if __name__ == "__main__":
    main()
