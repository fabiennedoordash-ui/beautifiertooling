#!/usr/bin/env python3
"""
beautify.py — Download catalog images and enhance them via OpenAI image edit.

Tries gpt-image-1 first, falls back to dall-e-2 if not available.
Converts all images to PNG before sending (dall-e-2 requires PNG).

Usage:
    python beautify.py --input urls.txt --output output/
    python beautify.py --input urls.csv --output output/ --prompt "custom prompt"
    python beautify.py --url "https://img.cdn4dd.com/..." --output output/
"""

import argparse
import base64
import csv
import os
import sys
import time
import json
import hashlib
from pathlib import Path
from io import BytesIO

import requests
from PIL import Image
from openai import OpenAI

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_PROMPT = (
    "Enhance this product photo for an e-commerce catalog. "
    "Place the product on a clean, pure white background. "
    "Ensure the product is well-lit, centered, and clearly visible. "
    "Remove any shelf tags, price stickers, store backgrounds, or clutter. "
    "Maintain the product's original colors and details accurately. "
    "Do not add any text, logos, or watermarks. "
    "Do not change the product itself — only improve the presentation."
)

MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_urls_from_txt(filepath: str) -> list[str]:
    """Load URLs from a plain text file, one per line."""
    urls = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if line and line.startswith("http"):
                urls.append(line)
    return urls


def load_urls_from_csv(filepath: str) -> list[str]:
    """Load URLs from a CSV. Looks for common column names."""
    url_columns = {"photo_url", "community_photo_url", "url", "image_url", "photo_urls"}
    urls = []
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        headers = {h.lower().strip() for h in reader.fieldnames} if reader.fieldnames else set()
        target_col = None
        for col in url_columns:
            if col in headers:
                for h in reader.fieldnames:
                    if h.lower().strip() == col:
                        target_col = h
                        break
                break
        if not target_col:
            print(f"ERROR: CSV has no recognized URL column. Found: {reader.fieldnames}")
            sys.exit(1)

        f.seek(0)
        reader = csv.DictReader(f)
        for row in reader:
            val = row.get(target_col, "").strip()
            if val and val.startswith("http"):
                urls.append(val)
    return urls


def load_urls(source: str) -> list[str]:
    """Load URLs from file based on extension."""
    ext = Path(source).suffix.lower()
    if ext == ".csv":
        return load_urls_from_csv(source)
    else:
        return load_urls_from_txt(source)


def url_to_filename(url: str) -> str:
    """Generate a short, unique filename from a URL."""
    parts = url.split("/")
    last = parts[-1] if parts else url
    name = last.replace("-retina-large", "")
    if len(name) > 60:
        name = hashlib.md5(url.encode()).hexdigest()[:16] + Path(name).suffix
    return name


def download_image(url: str) -> bytes | None:
    """Download image bytes from URL."""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"  DOWNLOAD FAILED: {e}")
        return None


def convert_to_png(image_bytes: bytes) -> bytes:
    """Convert any image format to RGBA PNG bytes (required by dall-e-2 edit)."""
    img = Image.open(BytesIO(image_bytes))
    img = img.convert("RGBA")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def detect_model(client: OpenAI, png_bytes: bytes, prompt: str) -> str:
    """
    Try gpt-image-1 first on the edit endpoint.
    If it fails with 'must be dall-e-2', fall back to dall-e-2.
    """
    try:
        print("  Probing gpt-image-1 support on images.edit...")
        result = client.images.edit(
            model="gpt-image-1",
            image=("image.png", png_bytes, "image/png"),
            prompt=prompt,
            size="1024x1024",
        )
        print("  gpt-image-1 is available — using it for all images.")
        return "gpt-image-1"
    except Exception as e:
        err_str = str(e)
        if "dall-e-2" in err_str or "invalid_value" in err_str.lower():
            print(f"  gpt-image-1 not available on edit endpoint, falling back to dall-e-2.")
            return "dall-e-2"
        else:
            print(f"  Probe failed ({e}), defaulting to dall-e-2.")
            return "dall-e-2"


def enhance_image(
    client: OpenAI,
    png_bytes: bytes,
    prompt: str,
    model: str,
) -> bytes | None:
    """Send PNG image to OpenAI for enhancement. Returns enhanced image bytes."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Pass as a named tuple so the SDK sends correct mimetype
            result = client.images.edit(
                model=model,
                image=("image.png", png_bytes, "image/png"),
                prompt=prompt,
                size="1024x1024",
            )

            # The response contains base64 image data
            enhanced_b64 = result.data[0].b64_json
            return base64.b64decode(enhanced_b64)

        except Exception as e:
            print(f"  API attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Beautify catalog product images using OpenAI image edit"
    )
    parser.add_argument(
        "--input", "-i",
        help="Path to .txt or .csv file containing image URLs",
    )
    parser.add_argument(
        "--url", "-u",
        help="Single image URL to process",
    )
    parser.add_argument(
        "--output", "-o",
        default="output",
        help="Output directory for enhanced images (default: output/)",
    )
    parser.add_argument(
        "--prompt", "-p",
        default=DEFAULT_PROMPT,
        help="Custom enhancement prompt",
    )
    parser.add_argument(
        "--model", "-m",
        default="auto",
        choices=["auto", "gpt-image-1", "dall-e-2"],
        help="Model to use: auto (try gpt-image-1, fallback dall-e-2), or force one",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download images but skip API calls (for testing)",
    )
    parser.add_argument(
        "--start-from",
        type=int,
        default=0,
        help="Skip the first N URLs (for resuming interrupted runs)",
    )
    args = parser.parse_args()

    # Collect URLs
    urls = []
    if args.url:
        urls = [args.url]
    elif args.input:
        urls = load_urls(args.input)
    else:
        print("ERROR: Provide either --input <file> or --url <url>")
        sys.exit(1)

    if not urls:
        print("No valid URLs found.")
        sys.exit(1)

    # Apply start-from offset
    if args.start_from > 0:
        print(f"Skipping first {args.start_from} URLs")
        urls = urls[args.start_from:]

    print(f"Found {len(urls)} image(s) to process")

    # Setup output dir
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    originals_dir = out_dir / "originals"
    originals_dir.mkdir(exist_ok=True)

    # Setup OpenAI client
    client = None
    if not args.dry_run:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("ERROR: OPENAI_API_KEY environment variable required")
            sys.exit(1)
        client = OpenAI(api_key=api_key)

    # Determine model to use
    model = args.model
    model_detected = False

    # Process results tracking
    results = []

    for idx, url in enumerate(urls):
        print(f"\n[{idx + 1}/{len(urls)}] Processing: {url[:80]}...")

        filename = url_to_filename(url)
        print(f"  Filename: {filename}")

        # Download
        image_bytes = download_image(url)
        if not image_bytes:
            results.append({"url": url, "status": "download_failed"})
            continue

        # Save original
        orig_path = originals_dir / filename
        with open(orig_path, "wb") as f:
            f.write(image_bytes)
        print(f"  Original saved: {orig_path}")

        if args.dry_run:
            results.append({"url": url, "status": "dry_run", "original": str(orig_path)})
            continue

        # Convert to PNG (required by dall-e-2, also works for gpt-image-1)
        try:
            png_bytes = convert_to_png(image_bytes)
            print(f"  Converted to PNG ({len(png_bytes):,} bytes)")
        except Exception as e:
            print(f"  PNG CONVERSION FAILED: {e}")
            results.append({"url": url, "status": "conversion_failed"})
            continue

        # Auto-detect model on first image
        if model == "auto" and not model_detected:
            model = detect_model(client, png_bytes, args.prompt)
            model_detected = True

        # Enhance
        print(f"  Enhancing via {model}...")
        enhanced_bytes = enhance_image(client, png_bytes, args.prompt, model)

        if enhanced_bytes:
            enhanced_filename = Path(filename).stem + "_enhanced.png"
            enhanced_path = out_dir / enhanced_filename
            with open(enhanced_path, "wb") as f:
                f.write(enhanced_bytes)
            print(f"  Enhanced saved: {enhanced_path}")
            results.append({
                "url": url,
                "status": "success",
                "model": model,
                "original": str(orig_path),
                "enhanced": str(enhanced_path),
            })
        else:
            print(f"  ENHANCEMENT FAILED after {MAX_RETRIES} attempts")
            results.append({"url": url, "status": "enhancement_failed", "model": model})

        # Rate limiting
        if idx < len(urls) - 1:
            time.sleep(2)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    success = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if "failed" in r["status"])
    print(f"  Model:     {model}")
    print(f"  Total:     {len(results)}")
    print(f"  Success:   {success}")
    print(f"  Failed:    {failed}")

    # Save results log
    log_path = out_dir / "results.json"
    with open(log_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Log saved: {log_path}")


if __name__ == "__main__":
    main()
