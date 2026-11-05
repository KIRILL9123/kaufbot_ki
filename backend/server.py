"""
Kleinanzeigen Hunter — FastAPI Server
Run with:  uvicorn server:app --reload --port 8080
"""
import asyncio, uuid, os, sys, logging, csv, io, json as _json
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler

sys.path.insert(0, os.path.dirname(__file__))
from scraper   import scrape_search
from ai_agent  import (check_ollama, best_model, analyze_listing,
                        negotiation_message, quick_batch_score)
from database  import (init_db, upsert_listing, save_analysis, get_listings,
                        get_listing, set_status, toggle_star, stats,
                        get_setting, set_setting, log_search,
                        get_scheduled_searches, save_scheduled_search,
                        delete_scheduled_search, update_scheduled_last_run,
                        mark_notified, get_price_history, get_analytics)
from notifier  import send_hot_listing

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")

# ── In-memory task store ────────────────────────────────────────────────────
TASKS: dict[str, dict] = {}

# ── Scheduler ───────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    for s in get_scheduled_searches():
        if s["active"]:
            scheduler.add_job(
                _scheduled_job, "interval",
                hours=s["interval_h"],
                id=f"search_{s['id']}",
                args=[s["id"]],
                replace_existing=True,
                misfire_grace_time=300,
            )
    scheduler.start()
    logger.info(f"Scheduler started — {len(scheduler.get_jobs())} job(s)")
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Kleinanzeigen Hunter", version="1.0", lifespan=lifespan)


# ── Pydantic models ─────────────────────────────────────────────────────────
class SearchReq(BaseModel):
    query: str
    max_pages: int = 2
    auto_analyze: bool = True

class StatusReq(BaseModel):
    status: str
    notes: Optional[str] = None

class MsgReq(BaseModel):
    listing_id: str
    target_price: Optional[float] = None

class SettingsReq(BaseModel):
    ollama_model:      Optional[str]  = None
    min_score:         Optional[int]  = None
    max_pages:         Optional[int]  = None
    auto_analyze:      Optional[bool] = None
    telegram_token:    Optional[str]  = None
    telegram_chat_id:  Optional[str]  = None
    notify_min_score:  Optional[int]  = None
    claude_api_key:    Optional[str]  = None

class ScheduledSearchReq(BaseModel):
    query:      str
    interval_h: int = 4
    max_pages:  int = 2


# ── Scheduled background job ─────────────────────────────────────────────────
async def _scheduled_job(search_id: int):
    searches = get_scheduled_searches()
    s = next((x for x in searches if x["id"] == search_id), None)
    if not s or not s["active"]:
        return

    logger.info(f"Scheduled job #{search_id}: '{s['query']}'")
    try:
        ollama_ok, models = check_ollama()
        model = best_model(models) if ollama_ok else get_setting("ollama_model", "llama3.2")
        claude_key = get_setting("claude_api_key", "") or None

        listings = await asyncio.to_thread(scrape_search, s["query"], s["max_pages"])
        for item in listings:
            upsert_listing(item, s["query"])
        log_search(s["query"], len(listings))

        if ollama_ok or claude_key:
            for item in listings:
                try:
                    analysis = await asyncio.to_thread(
                        analyze_listing, item, model, claude_key)
                    msg = None
                    if analysis.get("score", 0) >= 65:
                        msg = await asyncio.to_thread(
                            negotiation_message, item, analysis, model, None, claude_key)
                    save_analysis(item["id"], analysis, msg)
                except Exception as e:
                    logger.error(f"Scheduled analyze error {item.get('id')}: {e}")
                await asyncio.sleep(0.3)

        # Telegram notifications for hot listings found in this run
        token   = get_setting("telegram_token", "")
        chat_id = get_setting("telegram_chat_id", "")
        min_sc  = int(get_setting("notify_min_score", "80"))

        if token and chat_id:
            for item in listings:
                fresh = get_listing(item["id"])
                if (fresh
                        and not fresh.get("notified")
                        and (fresh.get("ai_score") or 0) >= min_sc):
                    sent = await send_hot_listing(token, chat_id, fresh)
                    if sent:
                        mark_notified(fresh["id"])

        update_scheduled_last_run(search_id)
    except Exception as e:
        logger.error(f"Scheduled job #{search_id} failed: {e}")


# ── Manual search background job ────────────────────────────────────────────
async def _job(task_id: str, query: str, max_pages: int, auto_analyze: bool):
    t = TASKS[task_id]
    try:
        ollama_ok, models = check_ollama()
        model = best_model(models) if ollama_ok else get_setting("ollama_model", "llama3.2")
        claude_key = get_setting("claude_api_key", "") or None

        t.update(status="scraping", message=f"🔍 Suche nach '{query}'…")

        listings = await asyncio.to_thread(scrape_search, query, max_pages)
        t["found"] = len(listings)

        for item in listings:
            upsert_listing(item, query)
        log_search(query, len(listings))

        if not listings:
            t.update(status="done", message="Keine Anzeigen gefunden.")
            return

        if auto_analyze and (ollama_ok or claude_key):
            t.update(status="analyzing", message=f"🤖 Analysiere mit {'Claude' if not ollama_ok else model}…")
            for i, item in enumerate(listings):
                t["message"] = f"🤖 Analysiere {i+1}/{len(listings)}: {item['title'][:45]}…"
                try:
                    analysis = await asyncio.to_thread(
                        analyze_listing, item, model, claude_key)
                    msg = None
                    if analysis.get("score", 0) >= 65:
                        msg = await asyncio.to_thread(
                            negotiation_message, item, analysis, model, None, claude_key)
                    save_analysis(item["id"], analysis, msg)
                    t["analyzed"] = i + 1
                except Exception as e:
                    logger.error(f"Analyze error {item.get('id')}: {e}")
                await asyncio.sleep(0.3)

        t.update(status="done",
                 message=f"✅ Fertig — {len(listings)} gefunden, {t.get('analyzed',0)} analysiert")
    except Exception as e:
        logger.exception(e)
        t.update(status="error", message=f"❌ Fehler: {e}")


# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/api/status")
async def api_status():
    ok, models = check_ollama()
    model = best_model(models) if ok else get_setting("ollama_model", "llama3.2")
    return {"ollama": {"available": ok, "models": models, "active": model},
            "stats": stats()}


@app.post("/api/search")
async def api_search(req: SearchReq, bg: BackgroundTasks):
    tid = uuid.uuid4().hex[:8]
    TASKS[tid] = {"status": "pending", "query": req.query,
                  "found": 0, "analyzed": 0, "message": "Warte…"}
    bg.add_task(_job, tid, req.query, req.max_pages, req.auto_analyze)
    return {"task_id": tid}


@app.get("/api/task/{tid}")
async def api_task(tid: str):
    if tid not in TASKS:
        raise HTTPException(404, "Task not found")
    return TASKS[tid]


@app.get("/api/listings")
async def api_listings(
    status: Optional[str] = None,
    min_score: Optional[int] = None,
    q: Optional[str] = None,
    starred: bool = False,
    limit: int = 100,
    offset: int = 0,
):
    return get_listings(status=status, min_score=min_score,
                        query=q, starred=starred, limit=limit, offset=offset)


@app.get("/api/listing/{lid}")
async def api_listing(lid: str):
    item = get_listing(lid)
    if not item:
        raise HTTPException(404, "Not found")
    return item


@app.get("/api/listing/{lid}/price_history")
async def api_price_history(lid: str):
    return get_price_history(lid)


@app.post("/api/listing/{lid}/status")
async def api_set_status(lid: str, req: StatusReq):
    set_status(lid, req.status, req.notes)
    return {"ok": True}


@app.post("/api/listing/{lid}/star")
async def api_star(lid: str):
    return {"starred": toggle_star(lid)}


@app.post("/api/listing/{lid}/analyze")
async def api_analyze(lid: str):
    item = get_listing(lid)
    if not item:
        raise HTTPException(404, "Not found")
    ok, models = check_ollama()
    claude_key = get_setting("claude_api_key", "") or None
    if not ok and not claude_key:
        raise HTTPException(503, "Weder Ollama noch Claude API key verfügbar")
    model    = best_model(models) if ok else get_setting("ollama_model", "llama3.2")
    analysis = await asyncio.to_thread(analyze_listing, item, model, claude_key)
    msg = None
    if analysis.get("score", 0) >= 60:
        msg = await asyncio.to_thread(negotiation_message, item, analysis, model, None, claude_key)
    save_analysis(lid, analysis, msg)
    return {**analysis, "ai_message": msg}


@app.post("/api/listing/{lid}/message")
async def api_message(lid: str, req: MsgReq):
    item = get_listing(lid)
    if not item:
        raise HTTPException(404, "Not found")
    ok, models = check_ollama()
    claude_key = get_setting("claude_api_key", "") or None
    if not ok and not claude_key:
        raise HTTPException(503, "Weder Ollama noch Claude API key verfügbar")
    model = best_model(models) if ok else get_setting("ollama_model", "llama3.2")
    analysis = {
        "score":             item.get("ai_score", 50),
        "recommended_offer": item.get("ai_offer"),
        "reasoning":         item.get("ai_reasoning", ""),
    }
    msg = await asyncio.to_thread(
        negotiation_message, item, analysis, model, req.target_price, claude_key)
    save_analysis(lid, analysis, msg)
    return {"message": msg}


# ── Settings ─────────────────────────────────────────────────────────────────

@app.get("/api/settings")
async def api_get_settings():
    return {
        "ollama_model":     get_setting("ollama_model",     "llama3.2"),
        "min_score":        int(get_setting("min_score",    "0")),
        "max_pages":        int(get_setting("max_pages",    "2")),
        "auto_analyze":     get_setting("auto_analyze",     "true") == "true",
        "telegram_token":   get_setting("telegram_token",   ""),
        "telegram_chat_id": get_setting("telegram_chat_id", ""),
        "notify_min_score": int(get_setting("notify_min_score", "80")),
        "claude_api_key":   get_setting("claude_api_key",   ""),
    }


@app.post("/api/settings")
async def api_save_settings(req: SettingsReq):
    if req.ollama_model     is not None: set_setting("ollama_model",     req.ollama_model)
    if req.min_score        is not None: set_setting("min_score",        str(req.min_score))
    if req.max_pages        is not None: set_setting("max_pages",        str(req.max_pages))
    if req.auto_analyze     is not None: set_setting("auto_analyze",     str(req.auto_analyze).lower())
    if req.telegram_token   is not None: set_setting("telegram_token",   req.telegram_token)
    if req.telegram_chat_id is not None: set_setting("telegram_chat_id", req.telegram_chat_id)
    if req.notify_min_score is not None: set_setting("notify_min_score", str(req.notify_min_score))
    if req.claude_api_key   is not None: set_setting("claude_api_key",   req.claude_api_key)
    return {"ok": True}


# ── Scheduled searches ───────────────────────────────────────────────────────

@app.get("/api/scheduled")
async def api_get_scheduled():
    return get_scheduled_searches()


@app.post("/api/scheduled")
async def api_add_scheduled(req: ScheduledSearchReq):
    sid = save_scheduled_search(req.query, req.interval_h, req.max_pages)
    scheduler.add_job(
        _scheduled_job, "interval",
        hours=req.interval_h,
        id=f"search_{sid}",
        args=[sid],
        replace_existing=True,
        misfire_grace_time=300,
    )
    logger.info(f"Scheduled search added: '{req.query}' every {req.interval_h}h")
    return {"id": sid}


@app.delete("/api/scheduled/{sid}")
async def api_delete_scheduled(sid: int):
    delete_scheduled_search(sid)
    job_id = f"search_{sid}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    return {"ok": True}


@app.post("/api/scheduled/{sid}/run")
async def api_run_scheduled_now(sid: int, bg: BackgroundTasks):
    """Trigger a scheduled search immediately (manual run)."""
    searches = get_scheduled_searches()
    if not any(s["id"] == sid for s in searches):
        raise HTTPException(404, "Scheduled search not found")
    bg.add_task(_scheduled_job, sid)
    return {"ok": True}


# ── Telegram test ────────────────────────────────────────────────────────────

@app.post("/api/telegram/test")
async def api_telegram_test():
    token   = get_setting("telegram_token",   "")
    chat_id = get_setting("telegram_chat_id", "")
    if not token or not chat_id:
        raise HTTPException(400, "Token und Chat-ID in den Einstellungen speichern")
    test_listing = {
        "title":        "✅ Kleinanzeigen Hunter — Test erfolgreich!",
        "ai_score":     99,
        "ai_verdict":   "HEISS",
        "price_text":   "999 €",
        "location":     "Berlin",
        "ai_market_price": 1500.0,
        "ai_profit":    501.0,
        "ai_reasoning": "Dies ist eine Testbenachrichtigung. Benachrichtigungen funktionieren!",
        "url":          "https://www.kleinanzeigen.de",
        "image_url":    "",
    }
    ok = await send_hot_listing(token, chat_id, test_listing)
    if not ok:
        raise HTTPException(500, "Senden fehlgeschlagen — Token oder Chat-ID prüfen")
    return {"ok": True}


# ── Export ────────────────────────────────────────────────────────────────────

_EXPORT_COLS = [
    "id","title","url","price","price_text","is_negotiable","location",
    "description","search_query","scraped_at",
    "ai_score","ai_verdict","ai_price_ass","ai_market_price",
    "ai_offer","ai_resell","ai_profit","ai_reasoning","ai_risks","ai_category",
    "status","starred","notes","updated_at",
]

@app.get("/api/export")
async def api_export(
    format:    str           = "csv",
    status:    Optional[str] = None,
    min_score: Optional[int] = None,
    q:         Optional[str] = None,
    starred:   bool          = False,
):
    rows = get_listings(status=status, min_score=min_score,
                        query=q, starred=starred, limit=10000)
    if format == "json":
        content = _json.dumps(rows, ensure_ascii=False, indent=2)
        return StreamingResponse(
            iter([content]),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=listings.json"},
        )
    # CSV
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_EXPORT_COLS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=listings.csv"},
    )


# ── Analytics ─────────────────────────────────────────────────────────────────

@app.get("/api/analytics")
async def api_analytics():
    return get_analytics()


# Serve frontend static files
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
