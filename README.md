# Wardrobe

A personal wardrobe management app for cataloging clothes, building outfits, tracking style gaps, and evaluating new purchases with AI.

## Quick Start

```bash
# Install dependencies
python3 -m pip install flask python-dotenv anthropic ddgs

# Add your API key (optional — needed for Fit Check + shopping refresh)
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env.local

# Run
python3 app.py
# → http://localhost:5001
```

## What It Does

### Wardrobe Tab
Browse your entire wardrobe as a visual card grid. Cards show the garment photo (4:5 aspect, full-garment view), subcategory, brand, colors, and formality level. Click any card to open the detail view where you can edit all metadata inline.

- **Drag & drop** to reorder cards (desktop; order persists in localStorage)
- **Filters** by category, weather, formality, fit, and free-text search
- **Quick Add** (mobile "+" button) — snap a photo, pick a category, done. Edit details later.

### Outfits Tab
Build and save outfits by picking items into slots: top, bottom, shoes, outerwear. Each outfit gets a name and activity tag (casual, date, work, etc.). The grid shows a 2x2 photo mosaic of the outfit's items.

### Shelf Tab
Two zones: **In Rotation** (active wardrobe) and **House** (stored away). Drag items between zones to change their status. Zoom slider to adjust thumbnail size.

### Style Tab
AI-powered style management based on a personal style guide (`STYLE_GUIDE.md`):

- **Purchase list** — tiered recommendations for wardrobe gaps, each with 3 shopping links (curated across niche brands, sale sections, and secondhand platforms)
- **Refresh button** — per-section AI search that uses Claude + DuckDuckGo to find current product availability and prices. Includes curated brand lists for each category.
- **Cull list** — items flagged for toss (damaged) or donation (redundant)

### Fit Check Tab
Take a photo of a garment while shopping and get an AI assessment:
- **Score** (1-10) — how well it fits your wardrobe and style
- **Pairs with** — specific items from your wardrobe shown as thumbnails
- **Gaps filled** — which purchase priorities it addresses
- **Concerns** — overlap with existing items, wrong color palette, etc.
- **Quick add** — high-scoring items can be added to the wardrobe in one tap

## Architecture

Single-page app — no build tools, no frameworks.

| Layer | Tech | File |
|-------|------|------|
| Frontend | Vanilla HTML/CSS/JS | `static/index.html` |
| Backend | Flask (Python) | `app.py` |
| Database | SQLite | `wardrobe.db` |
| AI | Anthropic Claude API | via `anthropic` SDK |
| Search | DuckDuckGo | via `ddgs` package |
| Images | Local filesystem | `images/` directory |

## Database Schema

### `items`
| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | `W001`–`W999` (auto-assigned) |
| `category` | TEXT | `outerwear`, `top`, `bottom`, `shoes`, `accessory` |
| `subcategory` | TEXT | e.g. `blazer`, `chinos`, `loafer` |
| `colors` | TEXT | Pipe-delimited: `navy\|white` |
| `pattern` | TEXT | `solid`, `plaid`, `check`, `stripe`, `print`, etc. |
| `formality` | INT | 1 (casual) → 5 (formal) |
| `weather` | TEXT | Pipe-delimited: `hot\|warm\|mild\|cool\|cold` |
| `fit` | TEXT | `regular`, `slim`, `relaxed`, `athletic`, `classic` |
| `pairs_well_with` | TEXT | Pipe-delimited item IDs |
| `notes` | TEXT | Fabric, style details, condition |
| `brand` | TEXT | Brand name |
| `size_tag` | TEXT | Label size (e.g. `M`, `32x32`, `10`) |
| `image_url` | TEXT | Google Drive URL (legacy fallback) |
| `tailored` | INT | 0/1 — custom fit or altered |
| `status` | TEXT | `active` (in rotation) or `home` (stored) |

### `outfits`
| Column | Type | Description |
|--------|------|-------------|
| `id` | INT PK | Auto-increment |
| `name` | TEXT | Outfit name |
| `activity` | TEXT | `casual`, `work`, `formal`, `date`, etc. |
| `notes` | TEXT | Optional notes |
| `created_at` | TEXT | ISO datetime |

### `outfit_items`
| Column | Type | Description |
|--------|------|-------------|
| `outfit_id` | INT FK | References `outfits.id` |
| `item_id` | TEXT | References `items.id` |
| `slot` | TEXT | `top`, `bottom`, `shoes`, `outerwear` |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/items` | List all items (supports query filters) |
| GET | `/api/items/:id` | Get single item |
| POST | `/api/items` | Add item (FormData with optional image) |
| PUT | `/api/items/:id` | Update item |
| DELETE | `/api/items/:id` | Delete item |
| PATCH | `/api/items/:id/status` | Change item status (active/home) |
| GET | `/api/meta` | Categories, fits, weathers, formalities |
| GET | `/api/outfits` | List all outfits with items |
| POST | `/api/outfits` | Create outfit |
| PUT | `/api/outfits/:id` | Update outfit |
| DELETE | `/api/outfits/:id` | Delete outfit |
| GET | `/api/style-gaps` | Purchase recommendations + cull lists |
| POST | `/api/refresh-section` | AI-powered product search for a purchase category |
| POST | `/api/fit-check` | AI analysis of a photo against wardrobe + style |

## Mobile Support

The app is designed mobile-first for iPhone Safari:
- Bottom tab bar for thumb-reachable navigation
- 2-column card grid optimized for phone screens
- Full-screen detail modal with swipe left/right navigation
- 16px form inputs (prevents iOS auto-zoom)
- Camera capture via `<input capture="environment">` for Quick Add and Fit Check
- Touch-friendly weather pill toggles and action buttons

## Files

```
wardrobe/
  app.py              # Flask backend + AI endpoints
  static/index.html   # Full frontend (HTML + CSS + JS, single file)
  images/             # Local item photos (W001.jpg, etc.)
  wardrobe.db         # SQLite database (auto-created)
  STYLE_GUIDE.md      # Personal style guide (fed to AI)
  .env.local          # ANTHROPIC_API_KEY (not committed)
  .gitignore
  README.md
```

## Style Guide

`STYLE_GUIDE.md` defines the user's aesthetic ("Relaxed Ivy / Mediterranean Ease"), color system, silhouette rules, outfit architecture, and a prioritized purchase list. This file is referenced by the AI in both the shopping refresh and fit check features. It includes:

- Aesthetic identity and style references
- Color system (warm neutrals, accent colors, what to avoid)
- 8 style rules (e.g. "relaxed bottom, fitted top", "loafer as default shoe")
- Outfit templates for dressed up, going out, casual, and warm weather
- Purchase priority tiers with specific brand targets
- Current wardrobe assets that fit the aesthetic
- Items that work against the aesthetic

## History

Originally a Google Sheets + Google Drive workflow accessed via Claude mobile app. Migrated to a standalone Flask web app with local SQLite for faster iteration, then progressively enhanced with outfit building, shelf management, AI-powered shopping, and the fit checker.
