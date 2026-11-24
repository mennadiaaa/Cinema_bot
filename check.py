import json
import os
import re
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Branch pages. Each lists movies; each movie has its own page with showtimes.
BRANCHES = {
    "Cairo Festival City": "https://cfc.scenecinemas.com/home",
    "District 5": "https://district5.scenecinemas.com/home",
}

# Movies you want EVERY new booking day / showtime alert for (matched by part of the name,
# case-insensitive). All other movies only alert on "New movie" and "BOOKING OPEN".
WATCHLIST = ["doomsday", "dune"]

NTFY_TOPIC = os.environ["NTFY_TOPIC"].strip()
FILE = "seen.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

session = requests.Session()
session.headers.update(HEADERS)

def send(title, text, url=None):
    """Send a push notification through ntfy.sh. Returns True on success."""
    headers = {"Title": title, "Priority": "high", "Tags": "clapper"}
    if url:
        headers["Click"] = url  # tapping the notification opens the movie page
    for attempt in range(4):
        try:
            r = requests.post(
                f"https://ntfy.sh/{NTFY_TOPIC}",
                data=text.encode("utf-8"),
                headers=headers,
                timeout=(10, 30),
            )
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
    for attempt in range(3):
        try:
            r = session.get(url, timeout=(10, 25))
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            print(f"GET {url} attempt {attempt + 1} failed: {e}")
            time.sleep(2)
    return None


def list_movies(label, page):
    """Returns {movie_url: name} for one branch."""
    html = get_html(page)
    if html is None:
        raise RuntimeError(f"Could not reach {page}")
    print(label, "-> listing page OK")
    soup = BeautifulSoup(html, "html.parser")
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


def parse_movie_page(html):
    """Returns (dates, shows): bookable days (DD-MM-YYYY) and showtime ids on the open day."""
    soup = BeautifulSoup(html, "html.parser")
    dates = sorted(
        {li.get("id", "")[len("data-"):] for li in soup.select("li.calanderdays")
         if re.fullmatch(r"data-\d{2}-\d{2}-\d{4}", li.get("id", ""))},
        key=lambda d: d[6:] + d[3:5] + d[:2],
    )
    shows = sorted({a["href"].rsplit("showtime-", 1)[1]
                    for a in soup.select('a[href*="showtime-"]')})
    return dates, shows


def fetch_movie(url):
    """(dates, shows) for a movie page, or None if the page failed."""
    html = get_html(url)
    if html is None:
        return None
    return parse_movie_page(html)


def watched(name):
    return any(w.lower() in name.lower() for w in WATCHLIST)


def pretty(date):  # "24-12-2026" -> "24 Dec"
    d, m, _ = date.split("-")
    names = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{int(d)} {names[int(m) - 1]}"


def load_seen():
    """seen.json maps movie_url -> {"name", "dates", "shows"}."""
    try:
        with open(FILE) as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict) or not all(
        isinstance(v, dict) and "dates" in v for v in data.values()
    ):
        return {}  # old/unknown format -> treat as first run
    return data


def main():
    movies = {}  # url -> (name, branch)
    for label, page in BRANCHES.items():
        try:
            for url, name in list_movies(label, page).items():
                movies.setdefault(url, (name, label))
        except Exception as e:
            print(f"[{label}] failed: {e}")
    if not movies:
        print("No movies found from any branch.")
        sys.exit(1)

    info = {}
    for url in movies:
        info[url] = fetch_movie(url)
        time.sleep(0.3)

    seen = load_seen()
    new_seen = dict(seen)
    failed = 0

    if not seen:  # first run: one summary, no spam
        lines = []
        for url, (name, branch) in sorted(movies.items(), key=lambda kv: kv[1][0]):
            r = info[url]
            if r is None:
                lines.append(f"- {name} ({branch}): page error")
            elif not r[0]:
                lines.append(f"- {name} ({branch}): no booking days yet")
            else:
                lines.append(f"- {name} ({branch}): {len(r[0])} days "
                             f"({pretty(r[0][0])} to {pretty(r[0][-1])})")
        text = ("Movies and their bookable days:\n" + "\n".join(lines) +
                "\n\nYou'll be notified of new movies and when booking opens. Extra booking days/showtimes only for: " + ", ".join(WATCHLIST) + ".")
        if not send("Cinema bot is running", text):
            print("Notification failed - not saving seen.json so nothing is lost.")
            sys.exit(1)
        new_seen = {u: {"name": movies[u][0], "dates": r[0], "shows": r[1]}
                    for u, r in info.items() if r is not None}
    else:
        for url, (name, branch) in movies.items():
            r = info[url]
            if r is None:  # page failed this run; keep old info, retry next time
                continue
            dates, shows = r
            old = seen.get(url)
            msg = None
            if old is None:
                body = f"{name} ({branch})"
                if dates:
                    body += f" - bookable {pretty(dates[0])} to {pretty(dates[-1])}"
                msg = ("New movie", body)
            else:
                added_dates = [d for d in dates if d not in old["dates"]]
                added_shows = [x for x in shows if x not in old["shows"]]
                if dates and not old["dates"]:
                    msg = ("BOOKING OPEN",
                           f"{name} ({branch}): {pretty(dates[0])} to {pretty(dates[-1])}")
                elif watched(name) and added_dates:
                    msg = ("New booking days",
                           f"{name} ({branch}): " + ", ".join(pretty(d) for d in added_dates))
                elif watched(name) and added_shows:
                    msg = ("New showtimes",
                           f"{name} ({branch}): {len(added_shows)} added")
            if msg is None or send(msg[0], msg[1], url):
                new_seen[url] = {"name": name, "dates": dates, "shows": shows}
            else:
                failed += 1  # leave as-is so we retry next run

    print(f"Checked {len(movies)} movies, {failed} notifications failed.")
    with open(FILE, "w") as f:
        json.dump(new_seen, f, indent=2, sort_keys=True)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
