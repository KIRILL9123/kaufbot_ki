"""
Kleinanzeigen Hunter — Scraper
Scrapes listings from kleinanzeigen.de without any API key.

Phase 1 improvements:
  - Fallback-selector chains per field (logs which selector matched)
  - User-Agent pool (10 entries) + realistic browser headers
  - Optional proxy pool via PROXY_LIST env var (comma-separated http[s]://... URLs)
  - Randomised delays with optional session reuse
"""
import os
import re
import time
import random
import logging
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.kleinanzeigen.de"

# ── User-Agent pool (10 entries) ─────────────────────────────────────────────
_UA_POOL = [
    # Chrome 122 / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome 121 / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Chrome 120 / Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Firefox 123 / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    # Firefox 122 / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.3; rv:122.0) Gecko/20100101 Firefox/122.0",
    # Safari 17 / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Safari/605.1.15",
    # Edge 122 / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    # Chrome 119 / Android
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
    # Safari / iPhone
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
    # Chrome 118 / Windows (older)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
]

# ── Proxy pool (optional) ─────────────────────────────────────────────────────
# Set PROXY_LIST=http://proxy1:port,http://proxy2:port in environment
_PROXY_LIST: list[str] = [
    p.strip() for p in os.environ.get("PROXY_LIST", "").split(",") if p.strip()
]


def _proxies() -> Optional[dict]:
    if not _PROXY_LIST:
        return None
    url = random.choice(_PROXY_LIST)
    return {"http": url, "https": url}


def _headers(referer: str = BASE_URL) -> dict:
    ua = random.choice(_UA_POOL)
    is_firefox = "Firefox" in ua
    is_mobile  = "Mobile" in ua or "Android" in ua or "iPhone" in ua

    h = {
        "User-Agent":      ua,
        "Accept-Language": random.choice([
            "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            "de-DE,de;q=0.9,en;q=0.8",
            "de-AT,de;q=0.9,en;q=0.8",
        ]),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            if is_firefox else
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "Referer":         referer,
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control":   "max-age=0",
    }
    if not is_firefox and not is_mobile:
        h["Sec-Fetch-Dest"]  = "document"
        h["Sec-Fetch-Mode"]  = "navigate"
        h["Sec-Fetch-Site"]  = "same-origin"
        h["Sec-Fetch-User"]  = "?1"
        h["DNT"]             = str(random.randint(0, 1))
    return h


# ── Selector chains ───────────────────────────────────────────────────────────
# Each chain is a list of (selector_string, bs4_method_kwargs).
# The first match wins; the matched selector is logged at DEBUG level.

def _first(card, chains: list, attr: Optional[str] = None, field_name: str = "?"):
    """Try each CSS selector in order; return the first match."""
    for sel in chains:
        el = card.select_one(sel)
        if el:
            logger.debug(f"  [{field_name}] matched: {sel!r}")
            return el.get(attr, "").strip() if attr else el
    logger.debug(f"  [{field_name}] no match in {chains}")
    return None


_SEL_TITLE = [
    "a.ellipsis",
    "a[class*='title']",
    "h2 a",
    "h3 a",
    "a[href*='/s-anzeige/']",
]

_SEL_PRICE = [
    "p.aditem-main--middle--price-shipping--price",
    "[class*='price-shipping--price']",
    "[class*='aditem-main--middle--price']",
    "[class*='price']",
    "strong[class*='price']",
]

_SEL_LOCATION = [
    ".aditem-main--top--left",
    "[class*='locality']",
    "[class*='location']",
    "[class*='aditem-main--top']",
]

_SEL_DESCRIPTION = [
    "p.aditem-main--middle--description",
    "[class*='aditem-main--middle--description']",
    "[class*='description']",
    "p[class*='text']",
]

_SEL_IMAGE = [
    "img[src]",
    "img[data-src]",
    "img",
]

# Search-result card containers
_SEL_CARDS = [
    "article.aditem",
    "li.aditem",
    "[data-adid]",
    "article[class*='aditem']",
    "article",
]

# Detail-page selectors
_SEL_DETAIL_DESC = [
    "#viewad-description-text",
    "[class*='description-text']",
    "[id*='description']",
    "[class*='viewad-description']",
]

_SEL_DETAIL_DATE = [
    "#viewad-extra-info span",
    "[id*='viewad-extra-info'] span",
    "[class*='addetail-header--badge']",
    "span[class*='date']",
]

_SEL_DETAIL_IMAGES = [
    ".galleryimage-element img",
    "[class*='gallery'] img",
    "[class*='image-gallery'] img",
    "#viewad-image img",
]


# ── Price parser ──────────────────────────────────────────────────────────────

def _parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = text.replace(".", "").replace(",", ".")
    m = re.search(r"(\d[\d\.]*)", cleaned)
    return float(m.group(1)) if m else None


# ── Card parser ───────────────────────────────────────────────────────────────

def _parse_card(card) -> Optional[dict]:
    try:
        # ── title / URL / ID ──────────────────────────────────────────────────
        title_el = None
        for sel in _SEL_TITLE:
            el = card.select_one(sel)
            if el and el.get_text(strip=True):
                title_el = el
                logger.debug(f"  [title] matched: {sel!r}")
                break
        if not title_el:
            # Last-resort: any <a> with an /s-anzeige/ href
            title_el = card.find("a", href=re.compile(r"/s-anzeige/"))
        if not title_el:
            return None

        href  = title_el.get("href", "")
        url   = BASE_URL + href if href.startswith("/") else href
        id_m  = re.search(r"/(\d{5,})", href)
        lid   = id_m.group(1) if id_m else re.sub(r"[^a-z0-9]", "", href)[-16:]
        title = title_el.get_text(strip=True)
        if not title or not lid:
            return None

        # ── price ─────────────────────────────────────────────────────────────
        price_el = _first(card, _SEL_PRICE, field_name="price")
        pt = price_el.get_text(strip=True) if price_el else "k.A."
        px = _parse_price(pt)
        vb = "VB" in pt

        # ── location ──────────────────────────────────────────────────────────
        loc_el = _first(card, _SEL_LOCATION, field_name="location")
        location = loc_el.get_text(strip=True) if loc_el else "DE"
        # clip city noise — keep first meaningful part
        if "\n" in location:
            location = location.split("\n")[0].strip()

        # ── description ───────────────────────────────────────────────────────
        desc_el = _first(card, _SEL_DESCRIPTION, field_name="description")
        description = desc_el.get_text(strip=True)[:400] if desc_el else ""

        # ── image ─────────────────────────────────────────────────────────────
        img_el = _first(card, _SEL_IMAGE, field_name="image")
        image_url = ""
        if img_el:
            image_url = (img_el.get("src") or img_el.get("data-src") or "").strip()

        return {
            "id":            lid,
            "title":         title,
            "url":           url,
            "price":         px,
            "price_text":    pt,
            "is_negotiable": vb,
            "location":      location,
            "description":   description,
            "image_url":     image_url,
            "scraped_at":    datetime.now().isoformat(),
        }
    except Exception as e:
        logger.debug(f"Card parse error: {e}")
        return None


# ── Page fetcher ──────────────────────────────────────────────────────────────

def _fetch_page(url: str, referer: str = BASE_URL, retries: int = 2) -> Optional[str]:
    """Fetch a URL with retries.  Returns HTML text or None."""
    proxies = _proxies()
    for attempt in range(1, retries + 2):
        try:
            delay = random.uniform(1.8, 3.5) * attempt
            logger.debug(f"Sleeping {delay:.1f}s before fetch (attempt {attempt})")
            time.sleep(delay)
            resp = requests.get(
                url,
                headers=_headers(referer=referer),
                proxies=proxies,
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.text
            if resp.status_code in (403, 429):
                logger.warning(f"HTTP {resp.status_code} on {url} — backing off")
                time.sleep(random.uniform(5, 12))
            else:
                logger.warning(f"HTTP {resp.status_code} on {url}")
                break
        except requests.RequestException as e:
            logger.error(f"Request error (attempt {attempt}): {e}")
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def scrape_search(query: str, max_pages: int = 2) -> list[dict]:
    """Return list of raw listing dicts for a search query."""
    results, seen = [], set()
    q_slug = requests.utils.quote(query.replace(" ", "-").lower())

    for page in range(1, max_pages + 1):
        url = (
            f"{BASE_URL}/s-{q_slug}/k0"
            if page == 1
            else f"{BASE_URL}/s-{q_slug}/k0/seite:{page}"
        )
        referer = BASE_URL if page == 1 else f"{BASE_URL}/s-{q_slug}/k0"

        html = _fetch_page(url, referer=referer)
        if html is None:
            logger.warning(f"Page {page}: fetch failed — stopping")
            break

        soup = BeautifulSoup(html, "html.parser")

        # Card container selector chain
        cards = []
        for sel in _SEL_CARDS:
            cards = soup.select(sel)
            if cards:
                logger.debug(f"Page {page}: card selector {sel!r} → {len(cards)} cards")
                break

        logger.info(f"Page {page}: {len(cards)} cards found (url={url})")
        added = 0
        for card in cards:
            item = _parse_card(card)
            if item and item["title"] and item["id"] not in seen:
                seen.add(item["id"])
                results.append(item)
                added += 1

        if added < 3:
            logger.info(f"Page {page}: only {added} new items — stopping pagination")
            break

    logger.info(f"scrape_search({query!r}, pages={max_pages}) → {len(results)} total")
    return results


def fetch_detail(url: str) -> dict:
    """Fetch extra details from a single listing page."""
    html = _fetch_page(url)
    if not html:
        return {}
    try:
        soup = BeautifulSoup(html, "html.parser")

        desc_el = None
        for sel in _SEL_DETAIL_DESC:
            desc_el = soup.select_one(sel)
            if desc_el:
                logger.debug(f"[detail/desc] matched: {sel!r}")
                break

        imgs = []
        for sel in _SEL_DETAIL_IMAGES:
            els = soup.select(sel)
            if els:
                logger.debug(f"[detail/images] matched: {sel!r} → {len(els)} imgs")
                imgs = [i.get("src") or i.get("data-src", "") for i in els]
                break

        date_el = None
        for sel in _SEL_DETAIL_DATE:
            date_el = soup.select_one(sel)
            if date_el:
                logger.debug(f"[detail/date] matched: {sel!r}")
                break

        return {
            "full_description": desc_el.get_text(strip=True) if desc_el else "",
            "images":           [x for x in imgs if x],
            "posted_date":      date_el.get_text(strip=True) if date_el else "",
        }
    except Exception as e:
        logger.error(f"Detail fetch error: {e}")
        return {}
