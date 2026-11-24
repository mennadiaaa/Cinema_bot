"""VOX Cinemas Egypt watcher (separate from check.py so Scene is never affected).

Alerts: new movie on What's On, BOOKING OPEN (first bookable showtimes), and for
WATCHLIST movies: new booking days / newly bookable showtimes.
Needs env var NTFY_TOPIC. State is kept in vox_seen.json.
"""
import json
import os
import re
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://egy.voxcinemas.com"
PAGE = BASE + "/movies/whatson"
FILE = "vox_seen.json"

# Movies you want EVERY new booking day / bookable showtime alert for (part of the name).
WATCHLIST = ["doomsday", "dune"]

NTFY_TOPIC = os.environ["NTFY_TOPIC"].strip()
IGNORE_SLUGS = {"", "whatson", "comingsoon", "movies", "advance-booking"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE + "/",
}


def send(title, text, url=None):
    headers = {"Title": title, "Priority": "high", "Tags": "clapper"}
    if url:
        headers["Click"] = url
    for attempt in range(4):
        try:
            r = requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=text.encode("utf-8"),
                              headers=headers, timeout=(10, 30))
        except requests.RequestException as e:
            print(f"ntfy attempt {attempt + 1} failed: {e}")
            time.sleep(3)
            continue
        if r.ok:
            return True
        print("ntfy error:", r.status_code, r.text[:200])
        time.sleep(5)
    return False


def get_html(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=(8, 20))
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        print("GET failed:", url, e)
        return None


def watched(name):
    return any(w.lower() in name.lower() for w in WATCHLIST)


def list_movies(html):
    """{movie_url: name} from the What's On page html."""
    soup = BeautifulSoup(html, "html.parser")
    movies = {}
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


def parse_movie_page(html):
    """(days, bookable): day labels like 'Thu 17 Dec', and ids of showtimes that have a
    booking link on the open day (greyed-out 'unavailable' showtimes are not counted)."""
    soup = BeautifulSoup(html, "html.parser")
    days = [li.get_text(" ", strip=True) for li in soup.select("nav.date-filter li")]
    days = [d for d in days if d]
    bookable = sorted({
        li["data-id"] for li in soup.select("ol.showtimes li[data-id]")
        if li.find("a", href=re.compile(r"/booking/"))
    })
    return days, bookable


def load_seen():
    try:
        with open(FILE) as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict) or not all(
        isinstance(v, dict) and "days" in v for v in data.values()
    ):
        return {}
    return data


def main():
    html = get_html(PAGE)
    if html is None:
        print("VOX unreachable from here.")
        sys.exit(0)  # not an error for the workflow; Scene keeps working
    movies = list_movies(html)
    if not movies:
        print("VOX page loaded but no movies found - layout may have changed.")
        sys.exit(0)

    info = {}
    for url in movies:
        page = get_html(url)
        info[url] = parse_movie_page(page) if page else None
        time.sleep(0.3)

    seen = load_seen()
    new_seen = dict(seen)
    failed = 0

    if not seen:  # first run: one summary
        lines = []
        for url, name in sorted(movies.items(), key=lambda kv: kv[1]):
            r = info[url]
            if r is None:
                lines.append(f"- {name}: page error")
            else:
                lines.append(f"- {name}: {len(r[0])} days, {len(r[1])} bookable showtimes")
        text = ("Movies on VOX Egypt:\n" + "\n".join(lines) +
                "\n\nYou'll be notified of new movies and when booking opens. "
                "Extra booking days/showtimes only for: " + ", ".join(WATCHLIST) + ".")
        if not send("VOX bot is running", text):
            print("Notification failed - not saving so nothing is lost.")
            sys.exit(1)
        new_seen = {u: {"name": movies[u], "days": r[0], "book": r[1]}
                    for u, r in info.items() if r is not None}
    else:
        for url, name in movies.items():
            r = info[url]
            if r is None:
                continue
            days, book = r
            old = seen.get(url)
            msg = None
            if old is None:
                body = f"{name}" + (f" - {len(book)} bookable showtimes" if book else "")
                msg = ("VOX: new movie", body)
            else:
                added_days = [d for d in days if d not in old["days"]]
                added_book = [b for b in book if b not in old["book"]]
                if book and not old["book"]:
                    msg = ("VOX: BOOKING OPEN", f"{name}: {len(book)} showtimes bookable")
                elif watched(name) and added_days:
                    msg = ("VOX: new booking days", f"{name}: " + ", ".join(added_days))
                elif watched(name) and added_book:
                    msg = ("VOX: new showtimes", f"{name}: {len(added_book)} newly bookable")
            if msg is None or send(msg[0], msg[1], url):
                new_seen[url] = {"name": name, "days": days, "book": book}
            else:
                failed += 1

    print(f"VOX: checked {len(movies)} movies, {failed} notifications failed.")
    with open(FILE, "w") as f:
        json.dump(new_seen, f, indent=2, sort_keys=True)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
