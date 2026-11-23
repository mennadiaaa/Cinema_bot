import json
import os
import re
import sys
import time
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

TOKEN = os.environ["TOKEN"].strip()
CHAT_ID = os.environ["CHAT_ID"].strip()
# Diagnostics (lengths only). Expected: token 46 characters, chat id 10.
print("TOKEN length:", len(TOKEN), "| CHAT_ID length:", len(CHAT_ID))
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
    for attempt in range(4):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                data={"chat_id": CHAT_ID, "text": text},
                timeout=(10, 30),
            )
        except requests.RequestException as e:
            print(f"Telegram attempt {attempt + 1} failed: {e}")
            time.sleep(3)
            continue
        if r.ok:
            return True
        print("Telegram error:", r.status_code, r.text)
        if r.status_code == 429:
            try:
                wait = r.json().get("parameters", {}).get("retry_after", 10)
            except Exception:
                wait = 10
            time.sleep(min(int(wait) + 1, 60))
            continue
        return False  # e.g. 401 bad token / 400 bad chat id: retrying won't help
    return False


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

    failed = set()
    if first_run:
        ok = send(f"✅ Cinema bot is running. Tracking {len(all_movies)} listings on Scene Cinemas. "
                  "You'll be notified when a new movie appears.")
        if not ok:
            print("Telegram send failed - not saving seen.json so nothing is lost.")
            sys.exit(1)
    else:
        for url, (name, branch) in new.items():
            if not send(f"🎬 New at Scene Cinemas ({branch}): {name}\n{url}"):
                failed.add(url)

    print(f"Found {len(all_movies)} listings, {len(new)} new, {len(failed)} failed to send.")
    with open(FILE, "w") as f:
        json.dump(sorted((seen | set(all_movies)) - failed), f, indent=2)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
