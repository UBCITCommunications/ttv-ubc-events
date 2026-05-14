#!/usr/bin/env python3
"""
Fetches upcoming UBC events from events.ubc.ca, filters to the ones suitable
for signage (next 7 days, has image, in-person), and writes events.json.
"""
import json
import re
import sys
from datetime import datetime, timezone, timedelta

import requests


API_BASE = "https://events.ubc.ca/wp-json/tribe/events/v1/events"
DAYS_AHEAD = 7
PER_PAGE = 50
MAX_PAGES = 8   # safety cap — 8 * 50 = 400 events scanned

# WordPress sites commonly block the default python-requests user-agent.
# Use a normal-looking browser UA so the API treats us like any other reader.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0 Safari/537.36 UBC-Signage-Bot/1.0",
    "Accept": "application/json",
}


def strip_html(html):
    if not html:
        return ""
    # Decode the most common entities, strip tags, collapse whitespace
    text = re.sub(r"<[^>]+>", " ", html)
    text = (text.replace("&nbsp;", " ")
                .replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&#8217;", "’")
                .replace("&#8211;", "–")
                .replace("&#8220;", "“")
                .replace("&#8221;", "”")
                .replace("&quot;", '"'))
    return re.sub(r"\s+", " ", text).strip()


def truncate_at_word(s, max_len):
    if len(s) <= max_len:
        return s
    cut = s[:max_len]
    last_space = cut.rfind(" ")
    return cut[: last_space if last_space > 0 else max_len].rstrip() + "…"


def pick_image(event):
    """Pick the best image variant — prefer 'large' (1024) for signage."""
    img = event.get("image")
    if not img or not isinstance(img, dict):
        return None
    sizes = img.get("sizes", {}) or {}
    for key in ("large", "medium_large", "1536x1536"):
        if key in sizes and sizes[key].get("url"):
            return sizes[key]["url"]
    # fall back to the original
    return img.get("url")


def event_type(categories):
    """Pull a clean type label like 'Workshop' or 'Exhibit' from the Type-* category."""
    for c in categories or []:
        name = c.get("name", "")
        if name.startswith("Type – ") or name.startswith("Type - "):
            return name.split("–")[-1].split("-")[-1].strip()
    return "Event"


def fetch_page(page, start_date):
    params = {
        "per_page": PER_PAGE,
        "page": page,
        "start_date": start_date,
        "status": "publish",
    }
    r = requests.get(API_BASE, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    now = datetime.now(timezone.utc)
    start_str = now.strftime("%Y-%m-%d 00:00:00")
    cutoff = now + timedelta(days=DAYS_AHEAD)

    print(f"Fetching events from {start_str} through {cutoff.isoformat()}")

    raw = []
    for page in range(1, MAX_PAGES + 1):
        try:
            data = fetch_page(page, start_str)
        except requests.HTTPError as e:
            # API returns 400 once you page past the end
            print(f"  page {page}: {e} (stopping)")
            break
        events = data.get("events", [])
        if not events:
            break
        raw.extend(events)
        print(f"  page {page}: +{len(events)} (total {len(raw)})")
        if len(events) < PER_PAGE:
            break

    print(f"Fetched {len(raw)} events total. Now filtering…")

    filtered = []
    for e in raw:
        # Skip trashed
        if "__trashed" in (e.get("slug") or ""):
            continue

        # Must have an image
        img_url = pick_image(e)
        if not img_url:
            continue

        # Skip virtual / online-only events
        if e.get("is_virtual"):
            continue
        venue = e.get("venue") or {}
        venue_name = venue.get("venue", "") if isinstance(venue, dict) else ""
        if "online" in venue_name.lower() or "virtual" in venue_name.lower():
            continue

        # Must start within our window
        try:
            start = datetime.fromisoformat(e["utc_start_date"].replace(" ", "T") + "+00:00")
        except Exception:
            continue
        if start < now - timedelta(hours=1):
            continue
        if start > cutoff:
            continue

        # Build the clean record
        slim = {
            "id":       e["id"],
            "title":    e.get("title", "").strip(),
            "url":      e.get("url"),
            "start":    e.get("start_date"),       # local Vancouver time string
            "end":      e.get("end_date"),
            "all_day":  e.get("all_day", False),
            "cost":     e.get("cost") or "",
            "venue":    venue_name,
            "city":     venue.get("city", "") if isinstance(venue, dict) else "",
            "image":    img_url,
            "type":     event_type(e.get("categories")),
            "summary":  truncate_at_word(strip_html(e.get("description", "")), 240),
        }
        filtered.append(slim)

    # Sort by start time
    filtered.sort(key=lambda x: x["start"])

    out = {
        "generated_at": now.isoformat(),
        "window_days": DAYS_AHEAD,
        "count": len(filtered),
        "events": filtered,
    }

    with open("events.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(filtered)} events to events.json (of {len(raw)} fetched).")


if __name__ == "__main__":
    main()
