#!/usr/bin/env python3
"""GPU deal hunter.

Checks deal feeds (r/buildapcsales, Slickdeals), finds posts for the cards in
deals_config.json, and pushes an alert to your phone when one is at or below
your target price. Designed to run on a schedule (GitHub Actions).

Run `python deal_bot.py --test` to send a test notification.
"""
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
USER_AGENT = "gpu-deal-bot/1.0 (personal price alerts)"
SEEN_TTL_DAYS = 14

# Dollar amounts like $799, $799.99, $1,199.99
PRICE_RE = re.compile(r"\$\s?((?:\d{1,2},\d{3}|\d{2,4})(?:\.\d{1,2})?)")


def load_config():
    cfg = json.loads(CONFIG_FILE.read_text())
    cards = []
    for c in cfg["cards"]:
        cards.append({
            "name": c["name"],
            "target": float(c["target_price"]),
            "regex": re.compile(c["pattern"], re.IGNORECASE),
        })
    exclude = re.compile(r"\b(" + "|".join(cfg["exclude_words"]) + r")\b", re.IGNORECASE)
    return cfg, cards, exclude


def extract_price(title):
    """Lowest dollar amount >= $200 in the title (the final price after
    coupons/rebates is usually the lowest; small amounts are coupon values)."""
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


def main():
    if "--test" in sys.argv:
        notify("GPU deal bot test", "If you see this, alerts are working.", "https://ntfy.sh")
        print("Test notification sent.")
        return

    import feedparser
    import requests

    cfg, cards, exclude = load_config()
    seen = load_seen()
    alerts = 0

    for feed_url in cfg["feeds"]:
        try:
            resp = requests.get(feed_url, headers={"User-Agent": USER_AGENT}, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"[warn] couldn't read {feed_url}: {e}")
            continue

        for entry in feedparser.parse(resp.content).entries:
            key = entry.get("id") or entry.get("link")
            if not key or key in seen:
                continue
            seen[key] = time.time()

            title = html.unescape(entry.get("title", "")).strip()
            card = match_card(title, cards, exclude)
            if not card:
                continue
            price = extract_price(title)
            if price is None:
                print(f"[skip, no price] {title}")
                continue
            if price > card["target"]:
                print(f"[skip, ${price:.0f} > ${card['target']:.0f}] {title}")
                continue

            savings = card["target"] - price
            notify(
                f"{card['name']} for ${price:.0f}",
                f"${savings:.0f} under your ${card['target']:.0f} target.\n{title}",
                entry.get("link", feed_url),
            )
            alerts += 1
            print(f"[ALERT] {title}")

    save_seen(seen)
    print(f"Done. {alerts} alert(s) sent.")


if __name__ == "__main__":
    main()
