# beautifiertooling

Takes existing DoorDash catalog image URLs and enhances them via **gpt-image-1** — clean white backgrounds, better lighting, centered product, catalog-ready output.

## Quick Start

### Local
```bash
pip install -r requirements.txt
export OPENAI_API_KEY="sk-..."

# Single URL
python beautify.py --url "https://img.cdn4dd.com/..." --output output/

# Batch from file
python beautify.py --input batches/easter_urls.txt --output output/

# Dry run (download only, skip API)
python beautify.py --input batches/easter_urls.txt --output output/ --dry-run

# Resume from URL #20
python beautify.py --input batches/easter_urls.txt --output output/ --start-from 20

# Custom prompt
python beautify.py --input batches/easter_urls.txt --output output/ \
  --prompt "Remove background, place on white, enhance colors"
```

### GitHub Actions

Two workflows available:

1. **Beautify Catalog Images** — paste URLs directly into the workflow input
2. **Beautify Batch (from file)** — commit a URL file to `batches/`, reference the path

Both upload enhanced images as downloadable artifacts.

## Setup

1. Add `OPENAI_API_KEY` as a repository secret
2. Commit URL files to `batches/` directory
3. Trigger via Actions tab → workflow_dispatch

## Input Formats

- `.txt` — one URL per line
- `.csv` — auto-detects columns: `photo_url`, `community_photo_url`, `url`, `image_url`

## Output

```
output/
├── originals/          # Downloaded source images
│   ├── 6f82680d-....jpg
│   └── ...
├── 6f82680d-..._enhanced.png   # Enhanced versions
├── ...
└── results.json        # Processing log
```

## Default Enhancement Prompt

> Enhance this product photo for an e-commerce catalog. Place the product on a clean, pure white background. Ensure the product is well-lit, centered, and clearly visible. Remove any shelf tags, price stickers, store backgrounds, or clutter. Maintain the product's original colors and details accurately. Do not add any text, logos, or watermarks. Do not change the product itself — only improve the presentation.
