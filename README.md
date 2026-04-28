# Wardrobe

A clothing database built on Google Drive and Google Sheets, used to get daily outfit suggestions and identify wardrobe gaps for shopping — accessed via the Claude mobile app.

## How it works

Photos are taken in Apple Photos (Wardrobe album), exported, compressed, and uploaded to Google Drive. Claude classifies each item and writes metadata to a Google Sheet. From the phone, ask Claude to suggest an outfit and it can read the sheet and images directly.

## Google Drive

| Resource | ID / URL |
|---|---|
| Wardrobe folder | [`1CkxKr1UYXK4OagsYKscki7U4qnHGjr6q`](https://drive.google.com/drive/folders/1CkxKr1UYXK4OagsYKscki7U4qnHGjr6q) |
| Images subfolder | `1ihjlfuzhSRazb3X1bLtqvrVS8LQ9JDeg` |
| Wardrobe v3 (current sheet) | [`14dikKFfqs-9gGuu4fLE5WO6CnKBuf2E9gm9aIIL9BkM`](https://docs.google.com/spreadsheets/d/14dikKFfqs-9gGuu4fLE5WO6CnKBuf2E9gm9aIIL9BkM) |

## Sheet schema

| Column | Description |
|---|---|
| `id` | W001–W077 (sequential, never reused) |
| `category` | `top` / `bottom` / `outerwear` / `shoes` |
| `subcategory` | dress shirt, chinos, blazer, flannel, etc. |
| `colors` | pipe-delimited (e.g. `light blue\|white`) |
| `pattern` | solid / check / plaid / graphic / heather / etc. |
| `formality` | 1 (casual) → 5 (black tie) |
| `weather` | pipe-delimited: `hot` / `warm` / `mild` / `cool` / `cold` |
| `fit` | regular / slim / relaxed / athletic / classic |
| `pairs_well_with` | pipe-delimited W-IDs |
| `notes` | fabric, style details, notable features |
| `brand` | brand name where visible |
| `size_tag` | size from label — fill in manually |
| `image` | Google Drive view URL |

## Item count

| Category | Count |
|---|---|
| Tops | 51 (W010–W074, excl. outerwear) |
| Outerwear | 15 (W001–W009, W011, W017, W018, W023, W026, W028, W055, W059) |
| Bottoms | 3 (W075–W077) |
| Shoes | 14 (W029–W042) |
| **Total** | **77** |

## Adding new items

1. Take photos in Apple Photos → add to **Wardrobe** album (portrait orientation, hang on rack against plain background)
2. Tell Claude: "I added X new items to the Wardrobe album"
3. Claude will:
   - Export + compress new photos from the album (`sips`, q70, max 1200px)
   - You drag the JPEGs from `/tmp/wardrobe_pants/` (or equivalent) to the Drive images folder
   - Claude classifies each item visually and appends rows to the sheet
   - A new versioned sheet (`Wardrobe v4`, etc.) is uploaded to Drive

## Updating the sheet

Claude always **downloads the current sheet first**, then applies changes, then uploads a new version. This preserves any manual edits (e.g. `size_tag` values you've filled in).

## Local temp files

Compressed images land in `/tmp/wardrobe_*/` during processing. These are cleared on reboot — Drive is the source of truth.

## What's still missing

- **Jeans / casual pants** — needed before outfit suggestions work well for casual looks
- **Shorts** — for summer outfit suggestions  
- **`size_tag`** — fill in from labels, useful for online shopping gap analysis
