import os
import re
import csv
import json
import sqlite3
from datetime import date
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError
from flask import Flask, jsonify, request, send_from_directory, g
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local", override=True)

try:
    import anthropic
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    AI_ENABLED = bool(os.getenv("ANTHROPIC_API_KEY"))
except ImportError:
    AI_ENABLED = False

app = Flask(__name__, static_folder="static", static_url_path="")

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "wardrobe.db"
IMAGES_DIR = BASE_DIR / "images"
INSPO_DIR = BASE_DIR / "inspo"
CSV_PATH = Path("/tmp/wardrobe_v4_fixed.csv")

IMAGES_DIR.mkdir(exist_ok=True)
INSPO_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id              TEXT PRIMARY KEY,
            category        TEXT,
            subcategory     TEXT,
            colors          TEXT,
            pattern         TEXT,
            formality       INTEGER,
            weather         TEXT,
            fit             TEXT,
            pairs_well_with TEXT,
            notes           TEXT,
            brand           TEXT,
            size_tag        TEXT,
            image_url       TEXT,
            tailored        INTEGER DEFAULT 0
        )
    """)
    # Migrate: add tailored column if it doesn't exist yet
    try:
        db.execute("ALTER TABLE items ADD COLUMN tailored INTEGER DEFAULT 0")
        db.commit()
    except Exception:
        pass  # Column already exists

    # Migrate: add status column if it doesn't exist yet
    try:
        db.execute("ALTER TABLE items ADD COLUMN status TEXT DEFAULT 'active'")
        db.commit()
    except Exception:
        pass  # Column already exists

    # Outfits tables
    db.execute("""
        CREATE TABLE IF NOT EXISTS outfits (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT,
            activity    TEXT,
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS outfit_items (
            outfit_id   INTEGER,
            item_id     TEXT,
            slot        TEXT,
            FOREIGN KEY (outfit_id) REFERENCES outfits(id) ON DELETE CASCADE
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS outfit_wears (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            outfit_id   INTEGER NOT NULL,
            worn_date   TEXT NOT NULL DEFAULT (date('now')),
            FOREIGN KEY (outfit_id) REFERENCES outfits(id) ON DELETE CASCADE
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS inspirations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT DEFAULT '',
            source_url  TEXT DEFAULT '',
            notes       TEXT DEFAULT '',
            tags        TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    db.commit()

    # Import CSV if table is empty
    count = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    if count == 0 and CSV_PATH.exists():
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                db.execute("""
                    INSERT OR IGNORE INTO items
                    (id, category, subcategory, colors, pattern, formality,
                     weather, fit, pairs_well_with, notes, brand, size_tag, image_url)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    row["id"], row["category"], row["subcategory"],
                    row["colors"], row["pattern"],
                    int(row["formality"]) if row["formality"].isdigit() else None,
                    row["weather"], row["fit"], row["pairs_well_with"],
                    row["notes"], row["brand"], row["size_tag"], row["image"]
                ))
        db.commit()
        print(f"Imported {db.execute('SELECT COUNT(*) FROM items').fetchone()[0]} items from CSV")

    db.close()


def drive_url_to_thumbnail(url: str) -> str:
    """Convert a Drive view URL to an embeddable thumbnail URL."""
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return f"https://drive.google.com/thumbnail?id={m.group(1)}&sz=w400"
    return url


def item_to_dict(row) -> dict:
    d = dict(row)
    item_id = d["id"]
    # Prefer local image, fall back to Drive thumbnail
    local = IMAGES_DIR / f"{item_id}.jpg"
    if local.exists():
        mtime = int(local.stat().st_mtime)
        d["image_src"] = f"/images/{item_id}.jpg?v={mtime}"
    elif d.get("image_url"):
        d["image_src"] = drive_url_to_thumbnail(d["image_url"])
    else:
        d["image_src"] = None
    return d


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/images/<filename>")
def serve_image(filename):
    return send_from_directory(IMAGES_DIR, filename)


@app.route("/api/items", methods=["GET"])
def list_items():
    db = get_db()
    query = "SELECT * FROM items WHERE 1=1"
    params = []

    category = request.args.get("category")
    formality = request.args.get("formality")
    weather = request.args.get("weather")
    fit = request.args.get("fit")
    search = request.args.get("search")

    if category:
        query += " AND category = ?"
        params.append(category)
    if formality:
        query += " AND formality = ?"
        params.append(int(formality))
    if weather:
        query += " AND weather LIKE ?"
        params.append(f"%{weather}%")
    if fit:
        query += " AND fit = ?"
        params.append(fit)
    if search:
        query += " AND (id LIKE ? OR subcategory LIKE ? OR colors LIKE ? OR brand LIKE ? OR notes LIKE ?)"
        s = f"%{search}%"
        params.extend([s, s, s, s, s])

    query += " ORDER BY id"
    rows = db.execute(query, params).fetchall()
    return jsonify([item_to_dict(r) for r in rows])


@app.route("/api/items/<item_id>", methods=["GET"])
def get_item(item_id):
    db = get_db()
    row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(item_to_dict(row))


@app.route("/api/items", methods=["POST"])
def add_item():
    db = get_db()
    data = request.form.to_dict()
    file = request.files.get("image")

    # Auto-assign next ID
    if not data.get("id"):
        last = db.execute("SELECT id FROM items ORDER BY id DESC LIMIT 1").fetchone()
        if last:
            num = int(last["id"][1:]) + 1
        else:
            num = 1
        data["id"] = f"W{num:03d}"

    image_url = data.get("image_url", "")

    if file and file.filename:
        ext = Path(file.filename).suffix or ".jpg"
        filename = f"{data['id']}{ext}"
        file.save(IMAGES_DIR / filename)
        image_url = ""  # local takes priority, url not needed

    db.execute("""
        INSERT INTO items
        (id, category, subcategory, colors, pattern, formality,
         weather, fit, pairs_well_with, notes, brand, size_tag, image_url, tailored)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        data["id"], data.get("category"), data.get("subcategory"),
        data.get("colors"), data.get("pattern"),
        int(data["formality"]) if data.get("formality", "").isdigit() else None,
        data.get("weather"), data.get("fit"), data.get("pairs_well_with"),
        data.get("notes"), data.get("brand"), data.get("size_tag"), image_url,
        1 if data.get("tailored") in ("1", "true", True) else 0
    ))
    db.commit()

    row = db.execute("SELECT * FROM items WHERE id = ?", (data["id"],)).fetchone()
    return jsonify(item_to_dict(row)), 201


@app.route("/api/items/<item_id>", methods=["PUT"])
def update_item(item_id):
    db = get_db()
    data = request.form.to_dict()
    file = request.files.get("image")

    if file and file.filename:
        ext = Path(file.filename).suffix or ".jpg"
        filename = f"{item_id}{ext}"
        file.save(IMAGES_DIR / filename)

    image_url = data.get("image_url", "")

    db.execute("""
        UPDATE items SET
            category=?, subcategory=?, colors=?, pattern=?, formality=?,
            weather=?, fit=?, pairs_well_with=?, notes=?, brand=?, size_tag=?,
            image_url=?, tailored=?
        WHERE id=?
    """, (
        data.get("category"), data.get("subcategory"),
        data.get("colors"), data.get("pattern"),
        int(data["formality"]) if data.get("formality", "").isdigit() else None,
        data.get("weather"), data.get("fit"), data.get("pairs_well_with"),
        data.get("notes"), data.get("brand"), data.get("size_tag"),
        image_url, 1 if data.get("tailored") in ("1", "true", True) else 0,
        item_id
    ))
    db.commit()

    row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return jsonify(item_to_dict(row))


@app.route("/api/items/<item_id>/status", methods=["PATCH"])
def update_item_status(item_id):
    db = get_db()
    data = request.get_json()
    status = data.get("status", "active")
    if status not in ("active", "home"):
        return jsonify({"error": "Invalid status"}), 400
    db.execute("UPDATE items SET status=? WHERE id=?", (status, item_id))
    db.commit()
    return jsonify({"ok": True, "id": item_id, "status": status})


@app.route("/api/items/<item_id>", methods=["DELETE"])
def delete_item(item_id):
    db = get_db()
    db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    db.commit()
    # Remove local image if exists
    for ext in [".jpg", ".jpeg", ".png"]:
        p = IMAGES_DIR / f"{item_id}{ext}"
        if p.exists():
            p.unlink()
    return jsonify({"deleted": item_id})


@app.route("/api/outfits", methods=["GET"])
def list_outfits():
    db = get_db()
    outfits = db.execute("SELECT * FROM outfits ORDER BY created_at DESC").fetchall()
    result = []
    for o in outfits:
        od = dict(o)
        rows = db.execute(
            "SELECT oi.slot, oi.item_id, i.subcategory, i.category, i.colors, i.image_url "
            "FROM outfit_items oi JOIN items i ON i.id = oi.item_id WHERE oi.outfit_id = ?",
            (o["id"],)
        ).fetchall()
        od["items"] = []
        for r in rows:
            rd = dict(r)
            local = IMAGES_DIR / f"{rd['item_id']}.jpg"
            rd["image_src"] = f"/images/{rd['item_id']}.jpg" if local.exists() else (
                drive_url_to_thumbnail(rd["image_url"]) if rd.get("image_url") else None
            )
            od["items"].append(rd)
        # Wear tracking data
        wear = db.execute(
            "SELECT COUNT(*) as cnt, MAX(worn_date) as last "
            "FROM outfit_wears WHERE outfit_id = ?", (o["id"],)
        ).fetchone()
        od["wear_count"] = wear["cnt"]
        od["last_worn"] = wear["last"]
        result.append(od)
    return jsonify(result)


@app.route("/api/outfits", methods=["POST"])
def create_outfit():
    db = get_db()
    data = request.get_json()
    cur = db.execute(
        "INSERT INTO outfits (name, activity, notes) VALUES (?,?,?)",
        (data.get("name", ""), data.get("activity", ""), data.get("notes", ""))
    )
    outfit_id = cur.lastrowid
    for slot_item in data.get("items", []):
        db.execute(
            "INSERT INTO outfit_items (outfit_id, item_id, slot) VALUES (?,?,?)",
            (outfit_id, slot_item["item_id"], slot_item["slot"])
        )
    db.commit()
    return jsonify({"id": outfit_id}), 201


@app.route("/api/outfits/<int:outfit_id>", methods=["PUT"])
def update_outfit(outfit_id):
    db = get_db()
    data = request.get_json()
    db.execute(
        "UPDATE outfits SET name=?, activity=?, notes=? WHERE id=?",
        (data.get("name", ""), data.get("activity", ""), data.get("notes", ""), outfit_id)
    )
    db.execute("DELETE FROM outfit_items WHERE outfit_id=?", (outfit_id,))
    for slot_item in data.get("items", []):
        db.execute(
            "INSERT INTO outfit_items (outfit_id, item_id, slot) VALUES (?,?,?)",
            (outfit_id, slot_item["item_id"], slot_item["slot"])
        )
    db.commit()
    return jsonify({"id": outfit_id})


@app.route("/api/outfits/<int:outfit_id>", methods=["DELETE"])
def delete_outfit(outfit_id):
    db = get_db()
    db.execute("DELETE FROM outfit_wears WHERE outfit_id=?", (outfit_id,))
    db.execute("DELETE FROM outfit_items WHERE outfit_id=?", (outfit_id,))
    db.execute("DELETE FROM outfits WHERE id=?", (outfit_id,))
    db.commit()
    return jsonify({"deleted": outfit_id})


@app.route("/api/outfits/<int:outfit_id>/wear", methods=["POST"])
def log_outfit_wear(outfit_id):
    db = get_db()
    outfit = db.execute("SELECT id FROM outfits WHERE id = ?", (outfit_id,)).fetchone()
    if not outfit:
        return jsonify({"error": "Outfit not found"}), 404
    data = request.get_json(silent=True) or {}
    worn_date = data.get("date") or date.today().isoformat()
    cur = db.execute(
        "INSERT INTO outfit_wears (outfit_id, worn_date) VALUES (?, ?)",
        (outfit_id, worn_date)
    )
    db.commit()
    return jsonify({"ok": True, "outfit_id": outfit_id, "wear_id": cur.lastrowid}), 201


@app.route("/api/outfit-wears/<int:wear_id>", methods=["DELETE"])
def delete_outfit_wear(wear_id):
    db = get_db()
    db.execute("DELETE FROM outfit_wears WHERE id = ?", (wear_id,))
    db.commit()
    return jsonify({"deleted": wear_id})


@app.route("/api/item-wear-counts")
def item_wear_counts():
    db = get_db()
    rows = db.execute("""
        SELECT oi.item_id, COUNT(ow.id) as wear_count,
               MAX(ow.worn_date) as last_worn
        FROM outfit_items oi
        JOIN outfit_wears ow ON ow.outfit_id = oi.outfit_id
        GROUP BY oi.item_id
    """).fetchall()
    return jsonify({
        r["item_id"]: {"count": r["wear_count"], "last_worn": r["last_worn"]}
        for r in rows
    })


# ---------------------------------------------------------------------------
# Inspirations
# ---------------------------------------------------------------------------

@app.route("/inspo/<filename>")
def serve_inspo_image(filename):
    return send_from_directory(INSPO_DIR, filename)


def _inspo_image_srcs(inspo_id: int) -> list[str]:
    """Find all images for an inspiration entry.

    Supports single-image (``{id}.jpg``) and multi-image
    (``{id}_1.jpg``, ``{id}_2.jpg``, …) naming conventions.
    Returns a list of ``/inspo/…`` URL strings with cache-bust params.
    """
    srcs: list[str] = []
    # Check multi-image first (from X imports): {id}_1.jpg, {id}_2.jpg, …
    idx = 1
    while True:
        found = False
        for ext in (".jpg", ".png", ".webp"):
            p = INSPO_DIR / f"{inspo_id}_{idx}{ext}"
            if p.exists():
                mtime = int(p.stat().st_mtime)
                srcs.append(f"/inspo/{inspo_id}_{idx}{ext}?v={mtime}")
                found = True
                break
        if not found:
            break
        idx += 1
    if srcs:
        return srcs
    # Fall back to single image: {id}.jpg
    for ext in (".jpg", ".png", ".webp"):
        p = INSPO_DIR / f"{inspo_id}{ext}"
        if p.exists():
            mtime = int(p.stat().st_mtime)
            return [f"/inspo/{inspo_id}{ext}?v={mtime}"]
    return []


@app.route("/api/inspirations", methods=["GET"])
def list_inspirations():
    db = get_db()
    rows = db.execute("SELECT * FROM inspirations ORDER BY created_at DESC").fetchall()
    result = []
    for r in rows:
        d = dict(r)
        srcs = _inspo_image_srcs(d["id"])
        d["image_srcs"] = srcs
        d["image_src"] = srcs[0] if srcs else None  # backward compat
        result.append(d)
    return jsonify(result)


@app.route("/api/inspirations", methods=["POST"])
def create_inspiration():
    db = get_db()
    title = request.form.get("title", "")
    source_url = request.form.get("source_url", "")
    notes = request.form.get("notes", "")
    tags = request.form.get("tags", "")
    file = request.files.get("image")

    cur = db.execute(
        "INSERT INTO inspirations (title, source_url, notes, tags) VALUES (?,?,?,?)",
        (title, source_url, notes, tags)
    )
    inspo_id = cur.lastrowid

    if file and file.filename:
        ext = Path(file.filename).suffix.lower() or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            ext = ".jpg"
        filename = f"{inspo_id}{ext}"
        file.save(INSPO_DIR / filename)

    db.commit()

    row = db.execute("SELECT * FROM inspirations WHERE id = ?", (inspo_id,)).fetchone()
    d = dict(row)
    srcs = _inspo_image_srcs(inspo_id)
    d["image_srcs"] = srcs
    d["image_src"] = srcs[0] if srcs else None
    return jsonify(d), 201


@app.route("/api/inspirations/<int:inspo_id>", methods=["PUT"])
def update_inspiration(inspo_id):
    db = get_db()
    data = request.form.to_dict()
    file = request.files.get("image")

    if file and file.filename:
        # Remove old image
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            old = INSPO_DIR / f"{inspo_id}{ext}"
            if old.exists():
                old.unlink()
        ext = Path(file.filename).suffix.lower() or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            ext = ".jpg"
        file.save(INSPO_DIR / f"{inspo_id}{ext}")

    db.execute(
        "UPDATE inspirations SET title=?, source_url=?, notes=?, tags=? WHERE id=?",
        (data.get("title", ""), data.get("source_url", ""),
         data.get("notes", ""), data.get("tags", ""), inspo_id)
    )
    db.commit()

    row = db.execute("SELECT * FROM inspirations WHERE id = ?", (inspo_id,)).fetchone()
    d = dict(row)
    srcs = _inspo_image_srcs(inspo_id)
    d["image_srcs"] = srcs
    d["image_src"] = srcs[0] if srcs else None
    return jsonify(d)


@app.route("/api/inspirations/<int:inspo_id>", methods=["DELETE"])
def delete_inspiration(inspo_id):
    db = get_db()
    db.execute("DELETE FROM inspirations WHERE id = ?", (inspo_id,))
    db.commit()
    # Remove single image
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = INSPO_DIR / f"{inspo_id}{ext}"
        if p.exists():
            p.unlink()
    # Remove multi-images ({id}_1.jpg, {id}_2.jpg, …)
    idx = 1
    while True:
        found = False
        for ext in (".jpg", ".png", ".webp"):
            p = INSPO_DIR / f"{inspo_id}_{idx}{ext}"
            if p.exists():
                p.unlink()
                found = True
                break
        if not found:
            break
        idx += 1
    return jsonify({"deleted": inspo_id})


@app.route("/api/inspirations/from-url", methods=["POST"])
def import_inspiration_from_url():
    """Import inspiration images from an X/Twitter post URL."""
    data = request.get_json()
    url = (data.get("url") or "").strip()

    # Parse X/Twitter URL
    m = re.match(r"https?://(?:x|twitter)\.com/(\w+)/status/(\d+)", url)
    if not m:
        return jsonify({"error": "Only X/Twitter links are supported right now"}), 400

    username, tweet_id = m.group(1), m.group(2)

    # Fetch tweet data via fxtwitter API
    api_url = f"https://api.fxtwitter.com/{username}/status/{tweet_id}"
    try:
        req = Request(api_url, headers={"User-Agent": "Wardrobe/1.0"})
        with urlopen(req, timeout=15) as resp:
            tweet_data = json.loads(resp.read().decode())
    except (URLError, json.JSONDecodeError) as e:
        return jsonify({"error": f"Could not fetch post: {e}"}), 502

    tweet = tweet_data.get("tweet", {})
    text = tweet.get("text", "")
    author_name = tweet.get("author", {}).get("name", username)
    photos = tweet.get("media", {}).get("photos", [])

    if not photos:
        return jsonify({"error": "No images found in this post"}), 404

    db = get_db()

    # Create ONE entry for the whole post
    cur = db.execute(
        "INSERT INTO inspirations (title, source_url, notes, tags) VALUES (?,?,?,?)",
        (f"@{username}", url, text, "")
    )
    inspo_id = cur.lastrowid

    # Download all images as {id}_1.jpg, {id}_2.jpg, …
    saved = 0
    for i, photo in enumerate(photos, 1):
        img_url = photo.get("url", "")
        if not img_url:
            continue
        try:
            img_req = Request(img_url, headers={"User-Agent": "Wardrobe/1.0"})
            with urlopen(img_req, timeout=30) as img_resp:
                img_bytes = img_resp.read()
                content_type = img_resp.headers.get("Content-Type", "image/jpeg")
        except URLError:
            continue

        ext = ".jpg"
        if "png" in content_type:
            ext = ".png"
        elif "webp" in content_type:
            ext = ".webp"

        (INSPO_DIR / f"{inspo_id}_{i}{ext}").write_bytes(img_bytes)
        saved += 1

    if not saved:
        db.execute("DELETE FROM inspirations WHERE id = ?", (inspo_id,))
        db.commit()
        return jsonify({"error": "Could not download any images"}), 502

    db.commit()

    row = db.execute("SELECT * FROM inspirations WHERE id = ?",
                     (inspo_id,)).fetchone()
    d = dict(row)
    srcs = _inspo_image_srcs(inspo_id)
    d["image_srcs"] = srcs
    d["image_src"] = srcs[0] if srcs else None

    return jsonify({"created": d, "count": saved,
                    "author": author_name, "text": text}), 201


@app.route("/api/meta")
def meta():
    db = get_db()
    categories = [r[0] for r in db.execute("SELECT DISTINCT category FROM items WHERE category != '' AND category IS NOT NULL ORDER BY category").fetchall()]
    fits = [r[0] for r in db.execute("SELECT DISTINCT fit FROM items WHERE fit != '' ORDER BY fit").fetchall()]
    return jsonify({
        "categories": categories,
        "fits": fits,
        "weathers": ["hot", "warm", "mild", "cool", "cold"],
        "formalities": [1, 2, 3, 4, 5],
    })


@app.route("/api/style-gaps")
def style_gaps():
    db = get_db()

    def item_to_dict(row):
        d = dict(row)
        local = IMAGES_DIR / f"{d['id']}.jpg"
        d["image_src"] = f"/images/{d['id']}.jpg" if local.exists() else None
        return d

    # ── Cull: toss (damaged active items) ──────────────────────────────────
    cull_toss = []
    for row in db.execute("SELECT * FROM items WHERE notes LIKE '%grease%' AND status='active'").fetchall():
        d = item_to_dict(row)
        d["reason"] = "Grease stains noted in the description — clean or toss"
        cull_toss.append(d)

    # ── Cull: donate (redundant / rarely-worn) ──────────────────────────────
    donate_map = {
        "W039": "Water shoes — worn maybe once a year; niche enough to borrow",
        "W061": "Purple F1 novelty tee — doesn't fit the style identity",
        "W163": "Christmas shirt — seasonal; takes up space 11 months a year",
        "W165": "Sports graphic tee — rarely going-out appropriate",
        "W107": "Grey shorts, no brand, home — 5+ grey/black shorts already in active rotation",
        "W110": "Pajama pants — have W142 as a second pair; one is enough",
        "W114": "Nike pants, home — W106, W138, W146 all do the same job in active rotation",
        "W116": "Nike blue shorts, home — redundant with active options",
        "W117": "Nike mint shorts, home — both W117 and W149 ended up home; donate one",
        "W142": "Duplicate pajama pants — keeping W110; one pair is enough",
    }
    cull_donate = []
    for item_id, reason in donate_map.items():
        row = db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if row:
            d = item_to_dict(row)
            d["reason"] = reason
            cull_donate.append(d)

    # ── Purchase list ────────────────────────────────────────────────────────
    purchase = [
        {
            "tier": 1,
            "name": "Suede Loafer",
            "why": "Zero loafers in the wardrobe. Your tan Zara chelsea is doing 11 outfits solo — a loafer unlocks an entirely different going-out register and takes the pressure off one pair of fast-fashion shoes.",
            "brands": {
                "niche": ["Sebago Beacon", "Sebago Dan", "Mulo", "Alden 990", "G.H. Bass Weejuns Larson", "Samuel Windsor", "Carmina", "Grenson Peter", "Sanders", "Tricker's"],
                "sale": ["Banana Republic", "J.Crew", "Cole Haan", "Johnston & Murphy"],
                "secondhand": ["eBay", "Grailed", "Poshmark"],
            },
            "options": [
                {"brand": "Banana Republic", "name": "Asher Suede Penny Loafer — Stone Gray", "price": "$169.99", "url": "https://bananarepublic.gap.com/browse/product.do?pid=574251012"},
                {"brand": "J.Crew Factory", "name": "Suede Penny Loafer — Koala Beige", "price": "see site", "url": "https://factory.jcrew.com/p/mens/categories/clothing/suits-and-blazers/suede-penny-loafers/BF762"},
                {"brand": "eBay", "name": "Suede Penny Loafer Tan — Size 10 Secondhand", "price": "$40–80", "url": "https://www.ebay.com/sch/i.html?_nkw=men+suede+penny+loafer+tan+size+10&LH_ItemCondition=3000&_sacat=93427"},
            ],
        },
        {
            "tier": 1,
            "name": "Premium White T-Shirt",
            "why": "The American Giant tee is doing date night duty across 6 outfits. A heavier, better-fitting white tee is one of the highest ROI purchases in a wardrobe — it's in almost every casual going-out look.",
            "brands": {
                "niche": ["Buck Mason", "James Perse", "Sunspel", "Merz b. Schwanen", "Lady White Co", "Reigning Champ", "Velva Sheen", "Columbiaknit"],
                "sale": ["J.Crew", "Todd Snyder", "Club Monaco"],
                "secondhand": ["Grailed", "Nordstrom Rack"],
            },
            "options": [
                {"brand": "Buck Mason", "name": "Curved Hem Tee — White Slub (Sale)", "price": "$30–40", "url": "https://www.buckmason.com/collections/sale"},
                {"brand": "Nordstrom Rack", "name": "James Perse / Buck Mason — Discounted", "price": "$25–50", "url": "https://www.nordstromrack.com/brands/James+Perse"},
                {"brand": "Grailed", "name": "James Perse White Tee — Secondhand", "price": "$20–40", "url": "https://www.grailed.com/shop/t-shirts"},
            ],
        },
        {
            "tier": 1,
            "name": "Quality Merino Crewneck",
            "why": "Your going-out sweaters are Old Navy and no-brand. One quality piece in charcoal or black becomes the anchor of half your date night outfits and raises the quality ceiling of the entire wardrobe.",
            "brands": {
                "niche": ["NN07", "Norse Projects", "Sunspel", "Johnstons of Elgin", "Andersen-Andersen", "Margaret Howell", "Oliver Spencer", "Drake's", "Portuguese Flannel"],
                "sale": ["Banana Republic", "J.Crew", "Club Monaco", "Charles Tyrwhitt"],
                "secondhand": ["Grailed", "The RealReal"],
            },
            "options": [
                {"brand": "Banana Republic", "name": "Merino Crew-Neck Sweater — Charcoal Gray", "price": "$40.99", "url": "https://bananarepublic.gap.com/browse/product.do?pid=796005102"},
                {"brand": "J.Crew", "name": "Washable Merino Wool Crewneck — Grey/Black", "price": "see site", "url": "https://www.jcrew.com/p/mens/categories/clothing/sweaters/pullover/washable-merino-wool-crewneck-sweater/AD336"},
                {"brand": "Grailed", "name": "Todd Snyder / NN07 Merino — Secondhand", "price": "$50–100", "url": "https://www.grailed.com/shop/crewneck-sweatshirts"},
            ],
        },
        {
            "tier": 2,
            "name": "Chelsea Boot Upgrade",
            "why": "Zara suede is carrying 11 outfits including dinners and dates. A quality leather pair in dark brown or black adds a darker color register, photographs better, and will outlast the Zaras by years.",
            "brands": {
                "niche": ["R.M. Williams Craftsman", "Thursday Boot Co Duke", "Blundstone 585", "Loake Chatsworth", "Tricker's", "Sanders", "Cheaney"],
                "sale": ["Thursday Boot Co", "End Clothing", "Atterley"],
                "secondhand": ["eBay", "Grailed"],
            },
            "options": [
                {"brand": "eBay", "name": "Thursday Boot Co. Chelsea — Secondhand", "price": "$80–130", "url": "https://www.ebay.com/sch/i.html?_nkw=thursday+boot+chelsea&LH_ItemCondition=3000"},
                {"brand": "Grailed", "name": "Quality Leather Chelsea Boots — Secondhand", "price": "$80–150", "url": "https://www.grailed.com/shop/boots"},
                {"brand": "The RealReal", "name": "Men's Chelsea Boots — Secondhand", "price": "$100–200", "url": "https://www.therealreal.com/shop/men/shoes/boots"},
            ],
        },
        {
            "tier": 2,
            "name": "Unstructured Casual Blazer",
            "why": "Every blazer you own is a suit or work blazer. An unstructured sport coat in linen or cotton goes over a white tee for going out — a completely different use case that none of your current blazers can fill without looking like you came from the office.",
            "brands": {
                "niche": ["COS", "NN07", "Norse Projects", "Sandro", "AMI Paris", "Portuguese Flannel", "Corridor", "De Bonne Facture", "Drake's", "Beams Plus", "Oliver Spencer"],
                "sale": ["J.Crew", "Club Monaco", "Banana Republic"],
                "secondhand": ["Grailed", "The RealReal"],
            },
            "options": [
                {"brand": "J.Crew", "name": "Ludlow Slim Unstructured Blazer — Irish Linen", "price": "see site", "url": "https://www.jcrew.com/p/mens/categories/clothing/blazers/casual-blazers/ludlow-slim-fit-unstructured-blazer-in-irish-cotton-linen-blend/BW357"},
                {"brand": "Grailed", "name": "COS / Sandro Unstructured Blazer — Secondhand", "price": "$80–150", "url": "https://www.grailed.com/shop/sport-coats-and-blazers"},
                {"brand": "The RealReal", "name": "Men's Unstructured Sport Coat — Secondhand", "price": "$100–200", "url": "https://www.therealreal.com/shop/men/jackets-and-blazers"},
            ],
        },
        {
            "tier": 2,
            "name": "Olive Slim Chino",
            "why": "All chinos are khaki/tan or navy. An olive slim chino pairs with navy, cream, black, and white tops — more versatile for evening than cargo pants, and fills a real color gap.",
            "brands": {
                "niche": ["NN07", "Incotex", "Corridor", "Officine Générale", "Portuguese Flannel", "Sunflower", "OrSlow", "Albam"],
                "sale": ["Banana Republic", "J.Crew", "Club Monaco"],
                "secondhand": ["Grailed"],
            },
            "options": [
                {"brand": "J.Crew", "name": "484 Slim-fit Stretch Chino — Olive 32x32", "price": "$68.50", "url": "https://www.jcrew.com/p/mens/categories/clothing/pants-and-chinos/chino/484-slim-fit-stretch-chino-pant/AR885"},
                {"brand": "Banana Republic", "name": "Slim Chino Olive — Men's Sale", "price": "$40–60", "url": "https://bananarepublic.gap.com/browse/men/mens-sale?cid=26219"},
                {"brand": "Grailed", "name": "Slim Olive Chino — Secondhand", "price": "$20–50", "url": "https://www.grailed.com/shop/chinos"},
            ],
        },
        {
            "tier": 3,
            "name": "Quality Fitted Black LS Tee",
            "why": "Athletic tees are being pressed into going-out base layer duty. A proper long-sleeve in black with a good drape elevates the Leather Jacket Date, Mac Coat Evening, and bomber outfits significantly.",
            "brands": {
                "niche": ["Buck Mason", "James Perse", "Sunspel", "Reigning Champ", "Lady White Co", "Merz b. Schwanen"],
                "sale": ["J.Crew", "Todd Snyder", "Club Monaco"],
                "secondhand": ["Grailed"],
            },
            "options": [
                {"brand": "Buck Mason", "name": "Curved Hem Long Sleeve — Black (Sale)", "price": "$30–40", "url": "https://www.buckmason.com/collections/sale"},
                {"brand": "J.Crew", "name": "Long Sleeve T-Shirt — Black (Sale)", "price": "$15–25", "url": "https://www.jcrew.com/mens/Sale"},
                {"brand": "Grailed", "name": "James Perse Long Sleeve — Secondhand", "price": "$25–45", "url": "https://www.grailed.com/shop/t-shirts"},
            ],
        },
        {
            "tier": 3,
            "name": "Dark Indigo Denim Jacket",
            "why": "No denim jacket anywhere in the wardrobe. A dark slim-fit version layers over tees for casual going out in a different register from the leather jacket or bombers — lighter and more approachable.",
            "brands": {
                "niche": ["Levi's Type III Trucker", "Edwin", "Naked & Famous", "Iron Heart", "Studio D'Artisan", "OrSlow", "Oni Denim"],
                "sale": ["Levi's", "Madewell", "J.Crew"],
                "secondhand": ["eBay", "Grailed"],
            },
            "options": [
                {"brand": "eBay", "name": "Levi's Trucker Dark Wash — Secondhand", "price": "$25–50", "url": "https://www.ebay.com/sch/i.html?_nkw=levis+trucker+jacket+dark+indigo+men&LH_ItemCondition=3000"},
                {"brand": "Grailed", "name": "Dark Denim Jacket — Secondhand", "price": "$40–100", "url": "https://www.grailed.com/shop/denim-jackets"},
                {"brand": "J.Crew", "name": "Denim Jacket — Men's Sale Section", "price": "$60–90", "url": "https://www.jcrew.com/mens/Sale"},
            ],
        },
    ]

    return jsonify({"cull_toss": cull_toss, "cull_donate": cull_donate, "purchase": purchase})


STYLE_CONTEXT = """
User profile:
- Aesthetic: "Relaxed Ivy / Mediterranean Ease" — warm natural tones, cream, sand, tan, cognac, brown
- Shoe size: 10
- Clothing size: Large
- Pants: 32W × 32L
- Blazer: 42R
- Budget strategy: prioritise items currently on sale at quality brands OR good-value secondhand
- Good sale brands: Banana Republic (almost always 40-50% off), J.Crew (frequently 30-50% off)
- Good secondhand platforms: eBay (best for shoes), Grailed (best for menswear brands), The RealReal
"""

def _web_search(query: str, max_results: int = 6) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return json.dumps(results, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _run_refresh(section_name: str, section_why: str, tier: int,
                 notes: str, current_options: list, brands: dict) -> list | None:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    niche   = ", ".join(brands.get("niche", []))
    on_sale = ", ".join(brands.get("sale", []))
    secondhand = ", ".join(brands.get("secondhand", []))

    system = f"""You are a personal shopping assistant refreshing product recommendations for a wardrobe app.

{STYLE_CONTEXT}

For this category the curated brand lists are:
- Niche quality brands (prioritise these — search each one by name on sale AND secondhand): {niche}
- Reliable sale brands (check their men's sale sections): {on_sale}
- Secondhand platforms to search: {secondhand}

Search strategy:
1. Search specifically for the niche brands above — they won't appear in generic results but are exactly right for the aesthetic
2. For each niche brand, search "<brand> <item> sale" AND "<brand> <item> site:grailed.com" or site:ebay.com
3. For sale brands, search "<brand> <item> men's sale <current year>"
4. Include size in every search (size 10 shoes, Large tops, 32x32 pants, 42R blazers)
5. Return direct product page links, not category pages

After searching, return ONLY a valid JSON array of exactly 3 options — aim for a mix of niche, sale, and secondhand:
[
  {{"brand": "Brand Name", "name": "Specific Product — Colour", "price": "$XX or $XX–XX", "url": "https://..."}}
]
No prose, no markdown fences — raw JSON array only."""

    notes_line = f"\nUser notes: {notes}" if notes.strip() else ""
    user_msg = f"""Refresh shopping options for: {section_name}
Why it matters: {section_why}{notes_line}

Current (possibly stale) options:
{json.dumps(current_options, indent=2)}

Search the curated brand lists above and return 3 better, currently-available options. Return JSON only."""

    messages = [{"role": "user", "content": user_msg}]
    tools = [{
        "name": "web_search",
        "description": "Search the web for current product availability, sale prices, and direct links.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"}
            },
            "required": ["query"]
        }
    }]

    MAX_SEARCH_ROUNDS = 3

    for round_num in range(MAX_SEARCH_ROUNDS + 1):
        # After max search rounds, strip tools so model must answer
        active_tools = tools if round_num < MAX_SEARCH_ROUNDS else []
        if round_num == MAX_SEARCH_ROUNDS:
            messages.append({
                "role": "user",
                "content": "You have done enough searching. Now return the JSON array of 3 options only — no prose, no markdown.",
            })

        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1024,
            system=system,
            tools=active_tools if active_tools else anthropic.NOT_GIVEN,
            messages=messages,
        )

        if response.stop_reason == "tool_use" and round_num < MAX_SEARCH_ROUNDS:
            tool_results = []
            for block in response.content:
                if block.type == "tool_use" and block.name == "web_search":
                    result = _web_search(block.input["query"])
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        elif response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    match = re.search(r"\[.*?\]", block.text, re.DOTALL)
                    if match:
                        try:
                            return json.loads(match.group())
                        except json.JSONDecodeError:
                            pass
            break

    return None


@app.route("/api/refresh-section", methods=["POST"])
def refresh_section():
    if not AI_ENABLED:
        return jsonify({"error": "ANTHROPIC_API_KEY not set in .env.local"}), 503
    try:
        data = request.get_json()
        options = _run_refresh(
            section_name=data.get("section_name", ""),
            section_why=data.get("section_why", ""),
            tier=data.get("tier", 1),
            notes=data.get("notes", ""),
            current_options=data.get("current_options", []),
            brands=data.get("brands", {}),
        )
        if options is None:
            return jsonify({"error": "AI refresh did not return valid options"}), 500
        return jsonify({"options": options})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fit-check", methods=["POST"])
def fit_check():
    """Analyze a clothing photo against the user's wardrobe and style guide."""
    if not AI_ENABLED:
        return jsonify({"error": "ANTHROPIC_API_KEY not set in .env.local"}), 503

    file = request.files.get("image")
    if not file:
        return jsonify({"error": "No image provided"}), 400

    context_text = request.form.get("context", "")

    import base64
    image_data = base64.b64encode(file.read()).decode("utf-8")
    media_type = file.content_type or "image/jpeg"

    # Build wardrobe summary for context
    db = get_db()
    rows = db.execute(
        "SELECT id, category, subcategory, colors, formality, weather, brand, fit "
        "FROM items WHERE status='active' OR status IS NULL"
    ).fetchall()
    wardrobe_summary = [dict(r) for r in rows]

    # Get purchase gap names
    purchase_gaps = [
        "Suede Loafer", "Premium White T-Shirt", "Quality Merino Crewneck",
        "Chelsea Boot Upgrade", "Wide-Leg Pleated Trouser",
        "Olive Waxed Jacket (Barbour-style)", "Unstructured Linen/Seersucker Blazer",
        "Quality Brown Leather Belt",
    ]

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    system_prompt = f"""You are a personal wardrobe consultant. The user has photographed a clothing item
while shopping and wants to know if it's a good addition to their wardrobe.

{STYLE_CONTEXT}

Their current wardrobe (active items):
{json.dumps(wardrobe_summary)}

Purchase gaps they are trying to fill:
{json.dumps(purchase_gaps)}

Analyse the photo and return ONLY a valid JSON object (no markdown, no prose):
{{
  "score": <1-10 integer, how well this fits the wardrobe>,
  "verdict": "<short verdict, e.g. 'Strong addition', 'Worth considering', 'Skip it'>",
  "reasoning": "<2-3 sentences on why>",
  "pairs_with": ["<item IDs from wardrobe it pairs well with, max 5>"],
  "gaps_filled": ["<purchase gap names it addresses, if any>"],
  "concerns": ["<potential concerns, e.g. 'Similar to W023 in color'>"],
  "suggested_category": "<outerwear|top|bottom|shoes|accessory>",
  "suggested_subcategory": "<e.g. blazer, jeans, loafer>"
}}"""

    context_line = f"\nContext from user: {context_text}" if context_text.strip() else ""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": f"Analyse this clothing item. Should I buy it?{context_line}",
                    },
                ],
            }],
        )

        # Parse JSON from response
        for block in response.content:
            if hasattr(block, "text"):
                match = re.search(r"\{.*\}", block.text, re.DOTALL)
                if match:
                    try:
                        result = json.loads(match.group())
                        return jsonify(result)
                    except json.JSONDecodeError:
                        pass

        return jsonify({"error": "Could not parse AI response"}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    init_db()
    print("Wardrobe running at http://localhost:5001")
    port = int(os.getenv("PORT", 5001))
    app.run(debug=True, port=port)
