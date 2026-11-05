"""
Kleinanzeigen Hunter — AI Agent
Uses local Ollama (free, no cloud) to analyze listings and write negotiation messages.
Phase 2: Claude API (claude-haiku) as fallback when Ollama is unavailable.
"""
import requests, json, re, logging
from typing import Optional

logger = logging.getLogger(__name__)
OLLAMA_URL    = "http://localhost:11434"
CLAUDE_URL    = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL  = "claude-haiku-4-5-20251001"

SYSTEM = """Du bist ein erfahrener Schnäppchenjäger und Flohmarkt-Profi in Deutschland.
Du analysierst Kleinanzeigen und entscheidest blitzschnell ob sich ein Kauf zum Weiterverkauf lohnt.
Du kennst Marktpreise für Elektronik, Smartphones, Fahrräder, Autos, Möbel, PCs und Notebooks sehr gut.
Antworte immer auf Deutsch. Sei präzise, direkt und praktisch."""


# ── Ollama helpers ────────────────────────────────────────────────────────

def check_ollama() -> tuple[bool, list[str]]:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            return True, models
    except Exception:
        pass
    return False, []


def best_model(models: list[str]) -> str:
    pref = ["llama3.2", "llama3.1", "llama3", "mistral", "mixtral",
            "qwen2.5", "gemma2", "phi3", "llama2"]
    for p in pref:
        for m in models:
            if p in m.lower():
                return m
    return models[0] if models else "llama3.2"


def _call_ollama(prompt: str, model: str, system: str = SYSTEM,
                 max_tokens: int = 900) -> Optional[str]:
    try:
        payload = {
            "model": model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {"temperature": 0.65, "num_predict": max_tokens},
        }
        r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=120)
        if r.status_code == 200:
            return r.json().get("response", "").strip()
        logger.error(f"Ollama {r.status_code}: {r.text[:200]}")
    except requests.exceptions.ConnectionError:
        logger.warning("Ollama nicht erreichbar — Claude Fallback aktiv")
    except Exception as e:
        logger.error(f"Ollama call failed: {e}")
    return None


def _call_claude(prompt: str, api_key: str, system: str = SYSTEM,
                 max_tokens: int = 900) -> Optional[str]:
    """Call Anthropic claude-haiku as fallback LLM."""
    try:
        headers = {
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        }
        payload = {
            "model":      CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "system":     system,
            "messages":   [{"role": "user", "content": prompt}],
        }
        r = requests.post(CLAUDE_URL, headers=headers, json=payload, timeout=60)
        if r.status_code == 200:
            content = r.json().get("content", [])
            if content:
                return content[0].get("text", "").strip()
        logger.error(f"Claude API {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"Claude API call failed: {e}")
    return None


def _call(prompt: str, model: str, system: str = SYSTEM, max_tokens: int = 900,
          claude_api_key: Optional[str] = None) -> Optional[str]:
    """Try Ollama first; fall back to Claude if api_key is set."""
    result = _call_ollama(prompt, model, system, max_tokens)
    if result:
        return result
    if claude_api_key:
        logger.info("Falling back to Claude API")
        return _call_claude(prompt, claude_api_key, system, max_tokens)
    return None


def _extract_json(text: str) -> Optional[dict]:
    start, end = text.find("{"), text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    return None


# ── Core functions ────────────────────────────────────────────────────────

def analyze_listing(listing: dict, model: str,
                    claude_api_key: Optional[str] = None) -> dict:
    """Deep AI analysis of a single listing. Returns structured dict."""
    title   = listing.get("title", "")
    pt      = listing.get("price_text", "k.A.")
    price   = listing.get("price")
    vb      = listing.get("is_negotiable", False)
    desc    = listing.get("description", "")[:400]
    loc     = listing.get("location", "")

    prompt = f"""Analysiere diese Kleinanzeige für einen Wiederverkäufer:

Titel: {title}
Preis: {pt} {"(VB)" if vb else ""}
Ort: {loc}
Beschreibung: {desc}

Antworte NUR mit einem JSON-Objekt (kein Markdown, keine Erklärung):
{{
  "score": <0-100 Resell-Attraktivität>,
  "verdict": "<HEISS|GUT|OKAY|SCHLECHT>",
  "price_assessment": "<zu hoch|fair|günstig|sehr günstig>",
  "market_price": <geschätzter Marktpreis EUR oder null>,
  "recommended_offer": <empfohlenes Gebot EUR oder null>,
  "resell_price": <erzielbarer Wiederverkaufspreis EUR oder null>,
  "profit_est": <geschätzter Gewinn EUR oder null>,
  "reasoning": "<2-3 prägnante Sätze>",
  "risks": "<kurz>",
  "category": "<Kategorie>"
}}"""

    raw = _call(prompt, model, claude_api_key=claude_api_key)
    if raw:
        data = _extract_json(raw)
        if data:
            data.setdefault("score", 50)
            data.setdefault("verdict", "OKAY")
            data.setdefault("reasoning", "Keine Analyse")
            return data

    return _fallback(listing)


def negotiation_message(listing: dict, analysis: dict, model: str,
                         target_price: Optional[float] = None,
                         claude_api_key: Optional[str] = None) -> str:
    """Generate a friendly German negotiation message."""
    title  = listing.get("title", "den Artikel")
    pt     = listing.get("price_text", "")
    offer  = target_price or analysis.get("recommended_offer")
    if not offer and listing.get("price"):
        offer = round(listing["price"] * 0.78, -1)

    prompt = f"""Schreibe eine kurze, freundliche Verhandlungsnachricht für Kleinanzeigen.

Artikel: {title}
Angefragter Preis: {pt}
Mein Wunschpreis: {offer} € (wenn sinnvoll erwähnen)
Begründung intern: {analysis.get("reasoning", "")}

Regeln:
- Max 4-5 Sätze, locker aber respektvoll
- Zeige echtes Interesse am Artikel
- Erwähne Barzahlung / schnelle Abwicklung als Plus
- Falls Preis weit über Markt: kurz sachlich begründen OHNE arrogant zu klingen
- NUR die Nachricht ausgeben, keine Überschrift, kein "Hier ist die Nachricht:"
"""
    msg = _call(prompt, model, max_tokens=250, claude_api_key=claude_api_key)
    if msg:
        return msg.strip('"').strip()

    offer_str = f"Wäre {offer} € möglich?" if offer else "Haben Sie noch Spielraum beim Preis?"
    return (f"Hallo, ich bin sehr interessiert an {title}. "
            f"{offer_str} Ich würde bar zahlen und kann zeitnah vorbeikommen. Danke!")


def quick_batch_score(listings: list[dict], model: str,
                      claude_api_key: Optional[str] = None) -> list[dict]:
    """Quickly score up to 12 listings in one LLM call for initial ranking."""
    if not listings:
        return listings

    batch = listings[:12]
    lines = "\n".join(
        f"{i+1}. {l['title']} — {l['price_text']} | {l['description'][:80]}"
        for i, l in enumerate(batch)
    )

    prompt = f"""Bewerte diese {len(batch)} Kleinanzeigen für Wiederverkauf (Punkte 0-100).

{lines}

Antworte NUR mit JSON-Array (kein Markdown):
[{{"i":1,"score":80,"note":"günstig"}}, ...]"""

    raw = _call(prompt, model, max_tokens=400, claude_api_key=claude_api_key)
    if raw:
        start, end = raw.find("["), raw.rfind("]") + 1
        if start >= 0 and end > start:
            try:
                scores = json.loads(raw[start:end])
                for s in scores:
                    idx = s.get("i", 0) - 1
                    if 0 <= idx < len(batch):
                        batch[idx]["quick_score"] = s.get("score", 50)
                        batch[idx]["quick_note"]  = s.get("note", "")
            except Exception:
                pass

    return listings


def _fallback(listing: dict) -> dict:
    p = listing.get("price") or 0
    return {
        "score": 50, "verdict": "OKAY",
        "price_assessment": "unbekannt",
        "market_price": None,
        "recommended_offer": round(p * 0.80) if p else None,
        "resell_price": round(p * 1.30) if p else None,
        "profit_est": round(p * 0.30) if p else None,
        "reasoning": "KI nicht verfügbar — bitte manuell prüfen.",
        "risks": "Unbekannt", "category": "Unbekannt",
    }
