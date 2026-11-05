"""
Scraper unit tests — run with:  python -m pytest backend/tests/
No network I/O; all tests use local HTML fixtures.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bs4 import BeautifulSoup
from scraper import _parse_card, _parse_price, scrape_search
from unittest.mock import patch, MagicMock

# ── HTML fixtures ─────────────────────────────────────────────────────────────

# Modern kleinanzeigen.de card structure (primary selectors)
_CARD_PRIMARY = """
<article class="aditem" data-adid="123456789">
  <div class="aditem-image">
    <img src="https://img.kleinanzeigen.de/api/v1/prod-ads/images/abc.jpg" alt="thumb"/>
  </div>
  <div class="aditem-main">
    <div class="aditem-main--top">
      <span class="aditem-main--top--left">Berlin, Mitte</span>
    </div>
    <div class="aditem-main--middle">
      <h2><a class="ellipsis" href="/s-anzeige/iphone-15-pro/123456789-123-456">
        iPhone 15 Pro 256GB Spaceschwarz
      </a></h2>
      <p class="aditem-main--middle--description">
        Verkaufe mein iPhone 15 Pro. Kaum benutzt, wie neu. Mit OVP.
      </p>
      <p class="aditem-main--middle--price-shipping--price">
        899 € VB
      </p>
    </div>
  </div>
</article>
"""

# Fallback card — uses data-adid, alternate class names
_CARD_FALLBACK = """
<li class="aditem" data-adid="987654321">
  <div>
    <a class="aditem-title" href="/s-anzeige/macbook-air/987654321-123-999">
      MacBook Air M2
    </a>
    <span class="locality">Hamburg, Altona</span>
    <span class="aditem-price">1.199,00 €</span>
    <img data-src="https://example.com/mac.jpg"/>
  </div>
</li>
"""

# Minimal card — only a link with /s-anzeige/ pattern
_CARD_MINIMAL = """
<article data-adid="555000111">
  <h3>
    <a href="/s-anzeige/lego-star-wars/555000111-123-777">LEGO Star Wars Set</a>
  </h3>
  <strong class="price">45 €</strong>
</article>
"""

# Bad card — no usable link at all
_CARD_BROKEN = """
<article class="aditem">
  <div>Keine Daten</div>
</article>
"""

# A second primary-style card for multi-card page tests
_CARD_PRIMARY_2 = """
<article class="aditem" data-adid="111222333">
  <div class="aditem-main">
    <div class="aditem-main--top">
      <span class="aditem-main--top--left">München, Schwabing</span>
    </div>
    <div class="aditem-main--middle">
      <h2><a class="ellipsis" href="/s-anzeige/samsung-s24/111222333-123-789">
        Samsung Galaxy S24
      </a></h2>
      <p class="aditem-main--middle--description">Top Zustand, kaum genutzt.</p>
      <p class="aditem-main--middle--price-shipping--price">599 €</p>
    </div>
  </div>
</article>
"""

# Full search-results page with two same-type cards + banner (banner skipped)
_PAGE_HTML = f"""
<!DOCTYPE html><html><body>
  {_CARD_PRIMARY}
  <div class="aditem-banner">This should be skipped</div>
  {_CARD_PRIMARY_2}
</body></html>
"""


# ── _parse_price tests ────────────────────────────────────────────────────────

def test_parse_price_integer():
    assert _parse_price("899 €") == 899.0

def test_parse_price_decimal_comma():
    assert _parse_price("1.199,00 €") == 1199.0

def test_parse_price_vb():
    assert _parse_price("450 € VB") == 450.0

def test_parse_price_kostenlos():
    # "Zu verschenken" / free items
    assert _parse_price("0 €") == 0.0

def test_parse_price_none_on_empty():
    assert _parse_price("") is None
    assert _parse_price("k.A.") is None

def test_parse_price_no_number():
    assert _parse_price("VB") is None


# ── _parse_card tests ─────────────────────────────────────────────────────────

def _card(html: str):
    return BeautifulSoup(html, "html.parser").select_one("article, li")


def test_parse_card_primary_title():
    item = _parse_card(_card(_CARD_PRIMARY))
    assert item is not None
    assert "iPhone 15 Pro" in item["title"]


def test_parse_card_primary_id():
    item = _parse_card(_card(_CARD_PRIMARY))
    assert item["id"] == "123456789"


def test_parse_card_primary_price():
    item = _parse_card(_card(_CARD_PRIMARY))
    assert item["price"] == 899.0
    assert item["is_negotiable"] is True


def test_parse_card_primary_location():
    item = _parse_card(_card(_CARD_PRIMARY))
    assert "Berlin" in item["location"]


def test_parse_card_primary_description():
    item = _parse_card(_card(_CARD_PRIMARY))
    assert "iPhone" in item["description"]


def test_parse_card_primary_image():
    item = _parse_card(_card(_CARD_PRIMARY))
    assert item["image_url"].startswith("https://")


def test_parse_card_fallback_title():
    item = _parse_card(_card(_CARD_FALLBACK))
    assert item is not None
    assert "MacBook" in item["title"]


def test_parse_card_fallback_id():
    item = _parse_card(_card(_CARD_FALLBACK))
    assert item["id"] == "987654321"


def test_parse_card_fallback_price():
    item = _parse_card(_card(_CARD_FALLBACK))
    assert item["price"] == 1199.0


def test_parse_card_fallback_image_data_src():
    item = _parse_card(_card(_CARD_FALLBACK))
    assert "example.com" in item["image_url"]


def test_parse_card_minimal():
    item = _parse_card(_card(_CARD_MINIMAL))
    assert item is not None
    assert "LEGO" in item["title"]
    assert item["id"] == "555000111"
    assert item["price"] == 45.0


def test_parse_card_broken_returns_none():
    item = _parse_card(_card(_CARD_BROKEN))
    assert item is None


def test_parse_card_url_absolute():
    item = _parse_card(_card(_CARD_PRIMARY))
    assert item["url"].startswith("https://www.kleinanzeigen.de")


def test_parse_card_scraped_at_set():
    item = _parse_card(_card(_CARD_PRIMARY))
    assert item["scraped_at"]  # non-empty ISO timestamp


# ── scrape_search (mocked network) ───────────────────────────────────────────

def test_scrape_search_returns_listings():
    with patch("scraper._fetch_page", return_value=_PAGE_HTML) as mock_fetch:
        results = scrape_search("iphone", max_pages=1)
    mock_fetch.assert_called_once()
    assert len(results) == 2


def test_scrape_search_deduplication():
    """Duplicates across pages must be deduplicated."""
    with patch("scraper._fetch_page", return_value=_PAGE_HTML):
        results = scrape_search("iphone", max_pages=2)
    ids = [r["id"] for r in results]
    assert len(ids) == len(set(ids)), "duplicate IDs found"


def test_scrape_search_stops_on_fetch_failure():
    with patch("scraper._fetch_page", return_value=None):
        results = scrape_search("test", max_pages=3)
    assert results == []


def test_scrape_search_stops_when_few_new():
    """If a page yields < 3 new items, pagination stops early."""
    single_card_page = f"<!DOCTYPE html><html><body>{_CARD_PRIMARY}</body></html>"
    call_count = 0

    def mock_fetch(url, **kwargs):
        nonlocal call_count
        call_count += 1
        return single_card_page

    with patch("scraper._fetch_page", side_effect=mock_fetch):
        results = scrape_search("iphone", max_pages=5)

    # Should stop after page 1 (only 1 item < 3)
    assert call_count == 1
    assert len(results) == 1
