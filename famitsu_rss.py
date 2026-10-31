#!/usr/bin/env python3
"""Build RSS feeds for famitsu.com categories.

Famitsu publishes no RSS of its own. The site is a Next.js app, so its
article lists and article bodies can be read as JSON under
/_next/data/<buildId>/, the same approach as RSSHub's famitsu route.

Each run reads the previously published cache.json from the site, so only
articles it has not seen before are downloaded in full. If a category fails,
its previously published feed is kept.

Usage: python3 famitsu_rss.py --out site --base-url https://USER.github.io/REPO
"""

import argparse
import datetime as dt
import email.utils
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = "https://www.famitsu.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
ICON = BASE + "/img/1812/favicons/apple-touch-icon.png"
JST = dt.timezone(dt.timedelta(hours=9))
ATOM = "http://www.w3.org/2005/Atom"
DC = "http://purl.org/dc/elements/1.1/"

# Category code -> name shown until the site's own name is known.
CATEGORIES = {
    "new-article": "新着",
    "news": "ニュース",
    "switch": "Switch",
    "ps5": "PS5",
    "ps4": "PS4",
    "pc-game": "PCゲーム",
    "videos": "動画",
    "special-article": "特集・企画記事",
    "interview": "インタビュー",
    "event-report": "取材・リポート",
    "review": "レビュー",
    "indie-game": "インディーゲーム",
}

MAX_DETAIL_FETCHES = 120  # full-article downloads per run
REQUEST_GAP = 1.0  # seconds between requests to famitsu.com


def http_get(url, params=None, retries=3):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ja"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == retries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
        time.sleep(5 * (attempt + 1))


_last_request = 0.0


def famitsu_get(path, params=None):
    global _last_request
    wait = _last_request + REQUEST_GAP - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    try:
        return http_get(BASE + path, params)
    finally:
        _last_request = time.monotonic()


def get_previous(url):
    """Fetch a file from the currently published site, or None if absent."""
    try:
        return http_get(url, retries=2)
    except (urllib.error.URLError, TimeoutError):
        return None


def warn(msg):
    # Shows up as a yellow annotation on the GitHub Actions run.
    print(f"::warning::{msg}", file=sys.stderr)


def get_build_id():
    page = famitsu_get("/")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        raise RuntimeError("__NEXT_DATA__ not found on the famitsu.com homepage")
    return json.loads(m.group(1))["buildId"]


def parse_date(s):
    try:
        d = dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=JST)


def category_items(build_id, code):
    data = json.loads(famitsu_get(
        f"/_next/data/{build_id}/category/{code}/page/1.json",
        {"categoryCode": code, "pageNumber": 1},
    ))["pageProps"]
    name = (data.get("targetCategory") or {}).get("nameJa") or CATEGORIES[code]
    items = []
    for a in data["categoryArticleDataForPc"]:
        if a.get("advertiserName") or not a.get("publishedAt"):
            continue
        pub = a["publishedAt"][:7].replace("-", "")
        cats = [a["mainCategory"]["nameJa"]] + [c["nameJa"] for c in a.get("subCategories") or []]
        thumb = a.get("thumbnailUrl")
        items.append({
            "id": a["id"],
            "pub": pub,
            "title": a["title"],
            "link": f"{BASE}/article/{pub}/{a['id']}",
            "date": parse_date(a["publishedAt"]),
            "categories": list(dict.fromkeys(cats)),
            # Used until the full article has been downloaded.
            "description": (f'<img src="{thumb}"><br>' if thumb else "") + html.escape(a.get("description") or ""),
            "author": "",
        })
    if not items:
        raise RuntimeError("category page listed no articles")
    return name, items


TAGS = {
    "B": "<b>{text}</b>",
    "INTERVIEWEE": "<b>{text}</b>",
    "STRONG": "<b>{text}</b>",
    "HEAD": "<h2>{text}</h2>",
    "SHEAD": "<h3>{text}</h3>",
    "LINK_B": '<a href="{url}"><b>{text}</b></a><br>',
    "LINK_B_TAB": '<a href="{url}"><b>{text}</b></a><br>',
    "IMAGE": '<img src="{path}">',
    "NEWS": '<a href="{url}">{text}<br>{description}</a><br>',
    "HTML": "{text}",
    "ARTICLE_AD": "",
    "ARTICLE_2ND_BANNER": "",
}
LINK_TYPES = {"BUTTON", "BUTTON_ANDROID", "BUTTON_EC", "BUTTON_IOS", "BUTTON_QUESTION",
              "BUTTON_TAB", "LINK", "LINK_TAB"}


def render_block(c):
    """Turn one block of Famitsu's article JSON into HTML."""
    if isinstance(c, str):
        return c
    content = c.get("content")
    if isinstance(content, list):
        return "".join(render_block(x) for x in content if isinstance(x, str) or x.get("type"))
    fields = {
        "text": content or "",
        "url": (c.get("url") or "").replace('"', "&quot;"),
        "path": (c.get("path") or "").replace('"', "&quot;"),
        "description": c.get("description") or "",
    }
    t = c.get("type")
    if t in LINK_TYPES:
        return '<a href="{url}">{text}</a><br>'.format(**fields)
    # Plain-text types (STRING, CAPTION, TWITTER, ...) and any new ones.
    return TAGS.get(t, "<div><span>{text}</span></div>").format(**fields)


def article_detail(build_id, pub, article_id):
    d = json.loads(famitsu_get(
        f"/_next/data/{build_id}/article/{pub}/{article_id}.json",
        {"publicationDate": pub, "articleId": article_id},
    ))["pageProps"]["articleDetailData"]
    banner = d.get("ogpImageUrl") or d.get("thumbnailUrl")
    body = "".join(render_block(c) for page in d.get("content") or [] for c in page.get("contents") or [])
    authors = ", ".join(a["name_ja"] for a in d.get("authors") or [] if a.get("name_ja"))
    return {
        "description": (f'<img src="{banner}"><br>' if banner else "") + body,
        "author": authors or (d.get("user") or {}).get("name_ja", ""),
    }


def build_feed(code, name, items, base_url):
    ET.register_namespace("atom", ATOM)
    ET.register_namespace("dc", DC)
    rss = ET.Element("rss", version="2.0")
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = f"{name}の最新記事 | ファミ通.com"
    ET.SubElement(ch, "link").text = f"{BASE}/category/{code}/page/1"
    ET.SubElement(ch, "description").text = f"ファミ通.com「{name}」カテゴリの最新記事"
    ET.SubElement(ch, "language").text = "ja"
    dates = [i["date"] for i in items if i["date"]]
    if dates:
        # Newest article time rather than "now", so an unchanged feed stays byte-identical.
        ET.SubElement(ch, "lastBuildDate").text = email.utils.format_datetime(max(dates))
    ET.SubElement(ch, f"{{{ATOM}}}link", href=f"{base_url}/{code}.xml", rel="self",
                  type="application/rss+xml")
    img = ET.SubElement(ch, "image")
    ET.SubElement(img, "url").text = ICON
    ET.SubElement(img, "title").text = "ファミ通.com"
    ET.SubElement(img, "link").text = BASE
    for i in items:
        it = ET.SubElement(ch, "item")
        ET.SubElement(it, "title").text = i["title"]
        ET.SubElement(it, "link").text = i["link"]
        ET.SubElement(it, "guid", isPermaLink="true").text = i["link"]
        if i["date"]:
            ET.SubElement(it, "pubDate").text = email.utils.format_datetime(i["date"])
        if i["author"]:
            ET.SubElement(it, f"{{{DC}}}creator").text = i["author"]
        for c in i["categories"]:
            ET.SubElement(it, "category").text = c
        ET.SubElement(it, "description").text = i["description"]
    ET.indent(rss)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode") + "\n"


def build_index(feeds, base_url):
    rows = "\n".join(
        f'<li><span class="name">{html.escape(name)}</span>'
        f'<input readonly value="{base_url}/{code}.xml" onclick="this.select()"></li>'
        for code, name in feeds
    )
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ファミ通 RSS</title>
<style>
:root {{ color-scheme: light dark; --bg: #fff; --fg: #1a1a1a; --muted: #666; --line: #ddd; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #16181c; --fg: #e8e8e8; --muted: #9a9a9a; --line: #333; }} }}
body {{ background: var(--bg); color: var(--fg); font: 16px/1.6 system-ui, sans-serif;
       max-width: 44rem; margin: 2rem auto; padding: 0 16px; }}
p {{ color: var(--muted); }}
ul {{ list-style: none; padding: 0; }}
li {{ display: flex; flex-wrap: wrap; gap: .25rem 1rem; align-items: center;
      padding: .6rem 0; border-bottom: 1px solid var(--line); }}
.name {{ min-width: 9rem; font-weight: 600; }}
input {{ flex: 1; min-width: 0; font: 14px ui-monospace, monospace; padding: .35rem .5rem;
         border: 1px solid var(--line); border-radius: 4px; background: transparent; color: inherit; }}
</style>
</head>
<body>
<h1>ファミ通.com RSS</h1>
<p>Unofficial feeds built from <a href="{BASE}/">famitsu.com</a>, updated about once an hour.
Copy a URL into Feedly or Inoreader.</p>
<ul>
{rows}
</ul>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="directory to write the site into")
    ap.add_argument("--base-url", required=True, help="public URL the site is served from")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base_url = args.base_url.rstrip("/")

    try:
        cache = json.loads(get_previous(f"{base_url}/cache.json") or "{}")
    except json.JSONDecodeError:
        cache = {}
    build_id = get_build_id()

    new_cache, feeds, failed = {}, [], []
    budget = MAX_DETAIL_FETCHES
    for code in CATEGORIES:
        try:
            name, items = category_items(build_id, code)
        except Exception as e:
            warn(f"{code}: {e}")
            failed.append(code)
            previous = get_previous(f"{base_url}/{code}.xml")
            if previous:
                (out / f"{code}.xml").write_text(previous, encoding="utf-8")
                feeds.append((code, CATEGORIES[code]))
            continue
        for item in items:
            detail = new_cache.get(item["link"]) or cache.get(item["link"])
            if detail is None and budget > 0:
                budget -= 1
                try:
                    detail = article_detail(build_id, item["pub"], item["id"])
                except Exception as e:
                    warn(f"{item['link']}: {e}")
            if detail:
                new_cache[item["link"]] = detail
                item.update(detail)
        (out / f"{code}.xml").write_text(build_feed(code, name, items, base_url), encoding="utf-8")
        feeds.append((code, name))
        print(f"{code}: {len(items)} articles")

    # Only articles in this run's feeds are kept, so the cache cannot grow without bound.
    (out / "cache.json").write_text(json.dumps(new_cache, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    (out / "index.html").write_text(build_index(feeds, base_url), encoding="utf-8")
    print(f"downloaded {MAX_DETAIL_FETCHES - budget} new articles")
    if len(failed) == len(CATEGORIES):
        sys.exit("every category failed; leaving the published site as it is")


if __name__ == "__main__":
    main()
