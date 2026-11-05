"""
Kleinanzeigen Hunter — Database
SQLite storage, zero external dependencies.
"""
import sqlite3, os, json
from datetime import datetime
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "listings.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    db = _conn()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS listings (
        id              TEXT PRIMARY KEY,
        title           TEXT,
        url             TEXT,
        price           REAL,
        price_text      TEXT,
        is_negotiable   INTEGER DEFAULT 0,
        location        TEXT,
        description     TEXT,
        image_url       TEXT,
        search_query    TEXT,
        scraped_at      TEXT,

        ai_score        INTEGER,
        ai_verdict      TEXT,
        ai_price_ass    TEXT,
        ai_market_price REAL,
        ai_offer        REAL,
        ai_resell       REAL,
        ai_profit       REAL,
        ai_reasoning    TEXT,
        ai_risks        TEXT,
        ai_category     TEXT,
        ai_message      TEXT,
        analyzed_at     TEXT,

        status          TEXT DEFAULT 'new',
        starred         INTEGER DEFAULT 0,
        notes           TEXT,
        updated_at      TEXT
    );

    CREATE TABLE IF NOT EXISTS search_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        query       TEXT,
        found       INTEGER,
        ts          TEXT
    );

    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE TABLE IF NOT EXISTS scheduled_searches (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        query      TEXT NOT NULL,
        interval_h INTEGER DEFAULT 4,
        max_pages  INTEGER DEFAULT 2,
        active     INTEGER DEFAULT 1,
        last_run   TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS price_history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        listing_id TEXT NOT NULL,
        price      REAL,
        ts         TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_ph_listing ON price_history (listing_id, ts);
    """)
    # add notified column if missing (migration for existing DBs)
    try:
        db.execute("ALTER TABLE listings ADD COLUMN notified INTEGER DEFAULT 0")
        db.commit()
    except Exception:
        pass
    # defaults
    for k, v in [("ollama_model", "llama3.2"), ("min_score", "0"),
                 ("auto_analyze", "true"), ("max_pages", "2"),
                 ("telegram_token", ""), ("telegram_chat_id", ""),
                 ("notify_min_score", "80"), ("claude_api_key", "")]:
        db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (k, v))
    db.commit()
    db.close()


def upsert_listing(item: dict, query: str = ""):
    db = _conn()
    existing = db.execute("SELECT status, starred, notes, price FROM listings WHERE id=?",
                          (item["id"],)).fetchone()

    new_price = item.get("price")
    old_price  = existing["price"] if existing else None
    # Record price history on first insert or on price change
    price_changed = existing is None or (new_price is not None and new_price != old_price)

    db.execute("""
        INSERT OR REPLACE INTO listings
          (id,title,url,price,price_text,is_negotiable,location,description,
           image_url,search_query,scraped_at,
           status,starred,notes,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        item["id"], item["title"], item["url"],
        new_price, item.get("price_text", ""),
        1 if item.get("is_negotiable") else 0,
        item.get("location", ""), item.get("description", ""),
        item.get("image_url", ""), query, item.get("scraped_at", datetime.now().isoformat()),
        existing["status"] if existing else "new",
        existing["starred"] if existing else 0,
        existing["notes"]   if existing else None,
        datetime.now().isoformat(),
    ))

    if price_changed and new_price is not None:
        db.execute(
            "INSERT INTO price_history (listing_id, price, ts) VALUES (?,?,?)",
            (item["id"], new_price, datetime.now().isoformat()),
        )

    db.commit()
    db.close()


def save_analysis(lid: str, a: dict, msg: Optional[str] = None):
    db = _conn()
    db.execute("""
        UPDATE listings SET
          ai_score=?,ai_verdict=?,ai_price_ass=?,ai_market_price=?,
          ai_offer=?,ai_resell=?,ai_profit=?,ai_reasoning=?,
          ai_risks=?,ai_category=?,ai_message=?,analyzed_at=?,updated_at=?
        WHERE id=?
    """, (
        a.get("score"), a.get("verdict"), a.get("price_assessment"),
        a.get("market_price"), a.get("recommended_offer"),
        a.get("resell_price"), a.get("profit_est"),
        a.get("reasoning"), a.get("risks"), a.get("category"),
        msg, datetime.now().isoformat(), datetime.now().isoformat(), lid,
    ))
    db.commit()
    db.close()


def get_listings(status=None, min_score=None, query=None,
                 starred=False, limit=100, offset=0) -> list[dict]:
    db  = _conn()
    sql = "SELECT * FROM listings WHERE 1=1"
    p   = []
    if status:       sql += " AND status=?";               p.append(status)
    if min_score:    sql += " AND ai_score>=?";            p.append(min_score)
    if query:        sql += " AND (title LIKE ? OR description LIKE ?)"; p += [f"%{query}%"]*2
    if starred:      sql += " AND starred=1"
    sql += " ORDER BY COALESCE(ai_score,0) DESC, scraped_at DESC"
    sql += f" LIMIT {limit} OFFSET {offset}"
    rows = db.execute(sql, p).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_listing(lid: str) -> Optional[dict]:
    db  = _conn()
    row = db.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
    db.close()
    return dict(row) if row else None


def set_status(lid: str, status: str, notes: Optional[str] = None):
    db = _conn()
    if notes is not None:
        db.execute("UPDATE listings SET status=?,notes=?,updated_at=? WHERE id=?",
                   (status, notes, datetime.now().isoformat(), lid))
    else:
        db.execute("UPDATE listings SET status=?,updated_at=? WHERE id=?",
                   (status, datetime.now().isoformat(), lid))
    db.commit(); db.close()


def toggle_star(lid: str) -> bool:
    db  = _conn()
    db.execute("UPDATE listings SET starred=1-starred WHERE id=?", (lid,))
    db.commit()
    row = db.execute("SELECT starred FROM listings WHERE id=?", (lid,)).fetchone()
    db.close()
    return bool(row["starred"]) if row else False


def stats() -> dict:
    db = _conn()
    def q(sql): return db.execute(sql).fetchone()[0]
    s = {
        "total":     q("SELECT COUNT(*) FROM listings"),
        "analyzed":  q("SELECT COUNT(*) FROM listings WHERE ai_score IS NOT NULL"),
        "hot":       q("SELECT COUNT(*) FROM listings WHERE ai_score>=75"),
        "starred":   q("SELECT COUNT(*) FROM listings WHERE starred=1"),
        "contacted": q("SELECT COUNT(*) FROM listings WHERE status='contacted'"),
        "avg_score": q("SELECT ROUND(AVG(ai_score)) FROM listings WHERE ai_score IS NOT NULL"),
    }
    db.close()
    return s


def get_setting(key: str, default=None) -> Optional[str]:
    db  = _conn()
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    db.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    db = _conn()
    db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))
    db.commit(); db.close()


def log_search(query: str, found: int):
    db = _conn()
    db.execute("INSERT INTO search_log (query,found,ts) VALUES (?,?,?)",
               (query, found, datetime.now().isoformat()))
    db.commit(); db.close()


# ── Scheduled searches ──────────────────────────────────────────────────────

def get_scheduled_searches() -> list[dict]:
    db   = _conn()
    rows = db.execute("SELECT * FROM scheduled_searches ORDER BY id").fetchall()
    db.close()
    return [dict(r) for r in rows]


def save_scheduled_search(query: str, interval_h: int, max_pages: int, sid: int = None) -> int:
    db = _conn()
    if sid:
        db.execute("UPDATE scheduled_searches SET query=?,interval_h=?,max_pages=? WHERE id=?",
                   (query, interval_h, max_pages, sid))
        db.commit(); db.close()
        return sid
    cursor = db.execute(
        "INSERT INTO scheduled_searches (query,interval_h,max_pages,active,created_at) VALUES (?,?,?,1,?)",
        (query, interval_h, max_pages, datetime.now().isoformat()),
    )
    sid = cursor.lastrowid
    db.commit(); db.close()
    return sid


def delete_scheduled_search(sid: int):
    db = _conn()
    db.execute("DELETE FROM scheduled_searches WHERE id=?", (sid,))
    db.commit(); db.close()


def update_scheduled_last_run(sid: int):
    db = _conn()
    db.execute("UPDATE scheduled_searches SET last_run=? WHERE id=?",
               (datetime.now().isoformat(), sid))
    db.commit(); db.close()


def mark_notified(lid: str):
    db = _conn()
    db.execute("UPDATE listings SET notified=1 WHERE id=?", (lid,))
    db.commit(); db.close()


def get_price_history(lid: str) -> list[dict]:
    db   = _conn()
    rows = db.execute(
        "SELECT price, ts FROM price_history WHERE listing_id=? ORDER BY ts ASC",
        (lid,),
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_analytics() -> dict:
    """Aggregated data for the analytics tab."""
    db = _conn()

    # Status distribution
    status_rows = db.execute(
        "SELECT COALESCE(status,'new') as status, COUNT(*) as cnt FROM listings GROUP BY status"
    ).fetchall()
    status_dist = {r["status"]: r["cnt"] for r in status_rows}

    # Top 10 AI categories
    cat_rows = db.execute(
        "SELECT ai_category, COUNT(*) as cnt FROM listings "
        "WHERE ai_category IS NOT NULL AND ai_category != '' AND ai_category != 'Unbekannt' "
        "GROUP BY ai_category ORDER BY cnt DESC LIMIT 10"
    ).fetchall()
    top_categories = [{"category": r["ai_category"], "count": r["cnt"]} for r in cat_rows]

    # Avg score per day (last 30 days)
    score_rows = db.execute(
        "SELECT substr(scraped_at,1,10) as day, "
        "       ROUND(AVG(ai_score),1) as avg_score, COUNT(*) as cnt "
        "FROM listings WHERE ai_score IS NOT NULL "
        "  AND scraped_at >= date('now','-30 days') "
        "GROUP BY day ORDER BY day ASC"
    ).fetchall()
    score_trend = [{"day": r["day"], "avg": r["avg_score"], "cnt": r["cnt"]} for r in score_rows]

    # Scoring buckets (0-40, 41-60, 61-75, 76-100)
    def _cnt(sql): return db.execute(sql).fetchone()[0] or 0
    score_buckets = [
        {"label": "0–40",  "count": _cnt("SELECT COUNT(*) FROM listings WHERE ai_score BETWEEN 0  AND 40")},
        {"label": "41–60", "count": _cnt("SELECT COUNT(*) FROM listings WHERE ai_score BETWEEN 41 AND 60")},
        {"label": "61–75", "count": _cnt("SELECT COUNT(*) FROM listings WHERE ai_score BETWEEN 61 AND 75")},
        {"label": "76+",   "count": _cnt("SELECT COUNT(*) FROM listings WHERE ai_score >= 76")},
    ]

    # Profit potential summary
    profit_row = db.execute(
        "SELECT SUM(ai_profit) as total, COUNT(*) as cnt FROM listings "
        "WHERE ai_profit > 0 AND status = 'bought'"
    ).fetchone()
    profit_total = round(profit_row["total"] or 0, 2)
    profit_count = profit_row["cnt"] or 0

    # Recent activity: new listings per day last 14 days
    activity_rows = db.execute(
        "SELECT substr(scraped_at,1,10) as day, COUNT(*) as cnt "
        "FROM listings WHERE scraped_at >= date('now','-14 days') "
        "GROUP BY day ORDER BY day ASC"
    ).fetchall()
    activity = [{"day": r["day"], "cnt": r["cnt"]} for r in activity_rows]

    db.close()
    return {
        "status_dist":   status_dist,
        "top_categories": top_categories,
        "score_trend":   score_trend,
        "score_buckets": score_buckets,
        "profit_total":  profit_total,
        "profit_count":  profit_count,
        "activity":      activity,
    }
