"""Telegram push notifications via Bot API (uses requests, no extra deps)."""
import asyncio, logging
import requests as _req

logger = logging.getLogger(__name__)
_BASE = "https://api.telegram.org/bot{token}/{method}"


def _post(token: str, method: str, **kwargs) -> dict:
    url = _BASE.format(token=token, method=method)
    try:
        r = _req.post(url, json=kwargs, timeout=10)
        return r.json()
    except Exception as e:
        return {"ok": False, "description": str(e)}


async def send_hot_listing(token: str, chat_id: str, listing: dict) -> bool:
    """Format and send a hot-listing card to Telegram."""
    score   = listing.get("ai_score", 0)
    verdict = listing.get("ai_verdict", "")
    title   = listing.get("title", "")
    price   = listing.get("price_text") or "k.A."
    loc     = listing.get("location", "")
    market  = listing.get("ai_market_price")
    profit  = listing.get("ai_profit")
    reason  = (listing.get("ai_reasoning") or "")[:180]
    risks   = (listing.get("ai_risks") or "")[:100]
    url     = listing.get("url", "")
    image   = listing.get("image_url", "")

    emoji = {"HEISS": "🔥", "GUT": "✅", "OKAY": "🟡", "SCHLECHT": "❌"}.get(verdict, "📦")
    text  = f"{emoji} *{verdict}* — Score: {score}/100\n\n*{title}*\n💰 {price}  📍 {loc}"

    if market:
        text += f"\n📊 ~{market:.0f} €"
        if profit:
            text += f"  |  Gewinn ~{profit:.0f} €"
    if reason:
        text += f"\n\n💡 _{reason}_"
    if risks:
        text += f"\n⚠️ {risks}"

    kb = {"inline_keyboard": [[{"text": "🔗 Anzeige öffnen", "url": url}]]}

    if image:
        result = await asyncio.to_thread(
            _post, token, "sendPhoto",
            chat_id=chat_id, photo=image, caption=text,
            parse_mode="Markdown", reply_markup=kb,
        )
    else:
        result = await asyncio.to_thread(
            _post, token, "sendMessage",
            chat_id=chat_id, text=text,
            parse_mode="Markdown", reply_markup=kb,
        )

    if not result.get("ok"):
        logger.error(f"Telegram error: {result.get('description')}")
    return result.get("ok", False)
