#!/usr/bin/env python3
"""GPU deal hunter.

Checks deal sources for the cards in deals_config.json and pushes an alert to
your phone when one is at or below your target price. Skips deals that are
marked expired or are older than max_post_age_hours.

Sources:
  - Reddit subreddits (e.g. r/buildapcsales): reads the "Expired" flair
  - RSS feeds (e.g. Slickdeals searches)
  - Best Buy's official product API (optional; needs a free BESTBUY_API_KEY
    secret): live prices for in-stock cards straight from the retailer

Run `python deal_bot.py --test` to send a test notification.
"""
import calendar
import html
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG_FILE = ROOT / "deals_config.json"
SEEN_FILE = ROOT / "seen.json"
HEADERS = {"User-Agent": "gpu-deal-bot/1.1 (personal price alerts)"}
SEEN_TTL_DAYS = 14

# Dollar amounts like $799, $799.99, $1,199.99
PRICE_RE = re.compile(r"\$\s?((?:\d{1,2},\d{3}|\d{2,4})(?:\.\d{1,2})?)")


# ---------- matching ----------

def load_config():
    cfg = json.loads(CONFIG_FILE.read_text())
    cards = [{
        "name": c["name"],
        "target": float(c["target_price"]),
        "regex": re.compile(c["pattern"], re.IGNORECASE),
    } for c in cfg["cards"]]
    exclude = re.compile(r"\b(" + "|".join(map(re.escape, cfg["exclude_words"])) + r")\b",
                         re.IGNORECASE)
    return cfg, cards, exclude


def extract_price(title):
    """Lowest dollar amount >= $200 in the title (the final price after
    coupons is usually the lowest; small amounts are coupon values)."""
    amounts = [float(m.replace(",", "")) for m in PRICE_RE.findall(title)]
    amounts = [a for a in amounts if a >= 200]
    return min(amounts) if amounts else None


def match_card(title, cards, exclude):
    if exclude.search(title):
        return None
    for card in cards:  # config lists the most specific names first
        if card["regex"].search(title):
            return card
    return None


# ---------- sources ----------
# Each source returns a list of dicts:
#   key, title, link, published (unix time or None), expired (bool), price (float or None)

def fetch_rss(url, requests, feedparser, key_prefix=""):
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    items = []
    for e in feedparser.parse(resp.content).entries:
        key = e.get("id") or e.get("link")
        if not key:
            continue
        stamp = e.get("published_parsed") or e.get("updated_parsed")
        title = html.unescape(e.get("title", "")).strip()
        items.append({
            "key": key_prefix + key.removeprefix("t3_"),
            "title": title,
            "link": e.get("link", url),
            "published": calendar.timegm(stamp) if stamp else None,
            "expired": "expired" in title.lower(),
            "price": None,
        })
    return items


def fetch_reddit(sub, requests, feedparser):
    """Reddit's JSON includes the post flair, which is how r/buildapcsales marks
    expired deals. Falls back to the RSS feed (no flair) if JSON is blocked."""
    try:
        resp = requests.get(f"https://www.reddit.com/r/{sub}/new.json?limit=50&raw_json=1",
                            headers=HEADERS, timeout=20)
        resp.raise_for_status()
        items = []
        for child in resp.json()["data"]["children"]:
            d = child["data"]
            flair = (d.get("link_flair_text") or "").lower()
            items.append({
                "key": "reddit:" + d["id"],
                "title": d["title"],
                "link": d.get("url") or "https://www.reddit.com" + d["permalink"],
                "published": d.get("created_utc"),
                "expired": "expired" in flair or bool(d.get("removed_by_category")),
                "price": None,
            })
        return items
    except Exception as e:
        print(f"[warn] Reddit JSON for r/{sub} failed ({e}); using RSS instead")
        return fetch_rss(f"https://www.reddit.com/r/{sub}/new/.rss",
                         requests, feedparser, key_prefix="reddit:")


def fetch_bestbuy(api_key, searches, requests):
    items = []
    for term in searches:
        resp = requests.get(
            f"https://api.bestbuy.com/v1/products(search={term})",
            params={"apiKey": api_key, "format": "json", "pageSize": 50,
                    "show": "sku,name,salePrice,onlineAvailability,url"},
            headers=HEADERS, timeout=20)
        resp.raise_for_status()
        for p in resp.json().get("products", []):
            if not p.get("onlineAvailability") or not p.get("salePrice"):
                continue
            price = float(p["salePrice"])
            items.append({
                # key includes price: a new, lower price triggers a fresh alert
                "key": f"bestbuy:{p['sku']}:{price:.2f}",
                "title": "[Best Buy] " + p["name"],
                "link": p.get("url", "https://www.bestbuy.com"),
                "published": None,
                "expired": False,
                "price": price,
            })
        time.sleep(0.5)  # stay well under Best Buy's rate limit
    return items


# ---------- state + alerts ----------

def load_seen():
    try:
        seen = json.loads(SEEN_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    cutoff = time.time() - SEEN_TTL_DAYS * 86400
    return {k: v for k, v in seen.items() if v > cutoff}


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen, indent=0, sort_keys=True))


def notify(title, message, url):
    import requests

    sent = False
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        requests.post("https://ntfy.sh/", json={
            "topic": topic, "title": title, "message": message,
            "click": url, "tags": ["moneybag"], "priority": 4,
        }, timeout=15).raise_for_status()
        sent = True
    hook = os.environ.get("DISCORD_WEBHOOK")
    if hook:
        requests.post(hook, json={"content": f"**{title}**\n{message}\n{url}"},
                      timeout=15).raise_for_status()
        sent = True
    if not sent:
        print(f"[no notifier set] {title} | {message} | {url}")


def gather(cfg):
    import feedparser
    import requests

    items = []
    for sub in cfg.get("reddit_subreddits", []):
        try:
            items += fetch_reddit(sub, requests, feedparser)
        except Exception as e:
            print(f"[warn] couldn't read r/{sub}: {e}")
    for url in cfg.get("rss_feeds", []):
        try:
            items += fetch_rss(url, requests, feedparser)
        except Exception as e:
            print(f"[warn] couldn't read {url}: {e}")
    bby_key = os.environ.get("BESTBUY_API_KEY")
    if bby_key:
        try:
            items += fetch_bestbuy(bby_key, cfg.get("bestbuy_searches", []), requests)
        except Exception as e:
            print(f"[warn] Best Buy API failed: {e}")
    return items


def process(items, cfg, cards, exclude, seen, now=None):
    """Decide which items deserve an alert. Returns list of (card, price, item)."""
    now = now or time.time()
    max_age = float(cfg.get("max_post_age_hours", 12)) * 3600
    hits = []
    for item in items:
        if item["key"] in seen:
            continue
        seen[item["key"]] = now
        title = item["title"]
        if item["expired"]:
            print(f"[skip, expired] {title}")
            continue
        if item["published"] and now - item["published"] > max_age:
            print(f"[skip, too old] {title}")
            continue
        card = match_card(title, cards, exclude)
        if not card:
            continue
        price = item["price"] or extract_price(title)
        if price is None:
            print(f"[skip, no price] {title}")
            continue
        if price > card["target"]:
            print(f"[skip, ${price:.0f} > ${card['target']:.0f}] {title}")
            continue
        hits.append((card, price, item))
    return hits


def main():
    if "--test" in sys.argv:
        notify("GPU deal bot test", "If you see this, alerts are working.", "https://ntfy.sh")
        print("Test notification sent.")
        return

    cfg, cards, exclude = load_config()
    seen = load_seen()
    hits = process(gather(cfg), cfg, cards, exclude, seen)
    for card, price, item in hits:
        notify(
            f"{card['name']} for ${price:.0f}",
            f"${card['target'] - price:.0f} under your ${card['target']:.0f} target.\n{item['title']}",
            item["link"],
        )
        print(f"[ALERT] {item['title']}")
    save_seen(seen)
    print(f"Done. {len(hits)} alert(s) sent.")


if __name__ == "__main__":
    main()
