#!/usr/bin/env python3
"""
beautify.py — Download catalog images, enhance via OpenAI, upload to imgbb.

Supports two modes:
  1. CSV mode: Input CSV with PHOTO_URL column → outputs same CSV + BEAUTIFIED_URL column
  2. URL mode: Plain text file or single URL → outputs results.json

Usage:
    python beautify.py --input easter_skus.csv --output output/
    python beautify.py --input urls.txt --output output/
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
IMGBB_UPLOAD_URL = "https://api.imgbb.com/1/upload"

# Column names to look for as the photo URL source
URL_COLUMN_NAMES = {"photo_url", "community_photo_url", "url", "image_url", "photo_urls"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def detect_csv_url_column(filepath: str):
    """Check if file is a CSV with a recognized URL column. Returns column name or None."""
    try:
        with open(filepath, "r") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return None
            for h in reader.fieldnames:
                if h.lower().strip() in URL_COLUMN_NAMES:
                    return h
    except Exception:
        pass
    return None


def load_csv_rows(filepath: str, url_column: str):
    """Load all rows from CSV, returning (rows, url_column)."""
    rows = []
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def load_urls_from_txt(filepath: str) -> list[str]:
    """Load URLs from a plain text file, one per line."""
    urls = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if line and line.startswith("http"):
                urls.append(line)
    return urls


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
    """Convert any image format to RGBA PNG bytes."""
    img = Image.open(BytesIO(image_bytes))
    img = img.convert("RGBA")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def upload_to_imgbb(image_bytes: bytes, api_key: str, name: str = None) -> str | None:
    """Upload image bytes to imgbb and return the viewer URL."""
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        payload = {"key": api_key, "image": b64}
        if name:
            payload["name"] = name
        resp = requests.post(IMGBB_UPLOAD_URL, data=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("success"):
            return data["data"]["url"]
        else:
            print(f"  IMGBB upload failed: {data}")
            return None
    except Exception as e:
        print(f"  IMGBB upload error: {e}")
        return None


def detect_model(client: OpenAI, png_bytes: bytes, prompt: str) -> str:
    """Try gpt-image-1 first, fall back to dall-e-2."""
    try:
        print("  Probing gpt-image-1 support on images.edit...")
        client.images.edit(
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
            print("  gpt-image-1 not available on edit endpoint, falling back to dall-e-2.")
            return "dall-e-2"
        else:
            print(f"  Probe failed ({e}), defaulting to dall-e-2.")
            return "dall-e-2"


def enhance_image(client: OpenAI, png_bytes: bytes, prompt: str, model: str) -> bytes | None:
    """Send PNG image to OpenAI for enhancement. Returns enhanced image bytes."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = client.images.edit(
                model=model,
                image=("image.png", png_bytes, "image/png"),
                prompt=prompt,
                size="1024x1024",
            )
            enhanced_b64 = result.data[0].b64_json
            return base64.b64decode(enhanced_b64)
        except Exception as e:
            print(f"  API attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
    return None


def process_single_url(url, client, model, prompt, imgbb_key, out_dir, originals_dir, skip_upload):
    """Process one URL: download → convert → enhance → upload. Returns (enhanced_bytes, imgbb_url, status)."""
    filename = url_to_filename(url)
    print(f"  Filename: {filename}")

    # Download
    image_bytes = download_image(url)
    if not image_bytes:
        return None, None, "download_failed"

    # Save original
    orig_path = originals_dir / filename
    with open(orig_path, "wb") as f:
        f.write(image_bytes)
    print(f"  Original saved: {orig_path}")

    # Convert to PNG
    try:
        png_bytes = convert_to_png(image_bytes)
        print(f"  Converted to PNG ({len(png_bytes):,} bytes)")
    except Exception as e:
        print(f"  PNG CONVERSION FAILED: {e}")
        return None, None, "conversion_failed"

    # Enhance
    print(f"  Enhancing via {model}...")
    enhanced_bytes = enhance_image(client, png_bytes, prompt, model)
    if not enhanced_bytes:
        print(f"  ENHANCEMENT FAILED after {MAX_RETRIES} attempts")
        return None, None, "enhancement_failed"

    # Save enhanced
    enhanced_filename = Path(filename).stem + "_enhanced.png"
    enhanced_path = out_dir / enhanced_filename
    with open(enhanced_path, "wb") as f:
        f.write(enhanced_bytes)
    print(f"  Enhanced saved: {enhanced_path}")

    # Upload to imgbb
    ibb_url = None
    if not skip_upload and imgbb_key:
        img_name = Path(filename).stem + "_enhanced"
        print("  Uploading to imgbb...")
        ibb_url = upload_to_imgbb(enhanced_bytes, imgbb_key, name=img_name)
        if ibb_url:
            print(f"  imgbb link: {ibb_url}")
        else:
            print("  imgbb upload failed — image still saved locally")

    return enhanced_bytes, ibb_url, "success"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Beautify catalog product images using OpenAI image edit"
    )
    parser.add_argument("--input", "-i", help="Path to CSV or .txt file containing image URLs")
    parser.add_argument("--url", "-u", help="Single image URL to process")
    parser.add_argument("--output", "-o", default="output", help="Output directory (default: output/)")
    parser.add_argument("--prompt", "-p", default=DEFAULT_PROMPT, help="Custom enhancement prompt")
    parser.add_argument("--model", "-m", default="auto", choices=["auto", "gpt-image-1", "dall-e-2"],
                        help="Model to use: auto (try gpt-image-1, fallback dall-e-2), or force one")
    parser.add_argument("--dry-run", action="store_true", help="Download images but skip API calls")
    parser.add_argument("--start-from", type=int, default=0, help="Skip the first N rows/URLs")
    parser.add_argument("--skip-upload", action="store_true", help="Skip imgbb upload")
    args = parser.parse_args()

    if not args.input and not args.url:
        print("ERROR: Provide either --input <file> or --url <url>")
        sys.exit(1)

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

    # Setup imgbb
    imgbb_key = os.environ.get("IMGBB_API_KEY")
    if not imgbb_key and not args.skip_upload and not args.dry_run:
        print("WARNING: IMGBB_API_KEY not set — skipping uploads.")
        args.skip_upload = True

    # Model detection
    model = args.model
    model_detected = False

    # -----------------------------------------------------------------------
    # Detect mode: CSV with structured data vs plain URL list
    # -----------------------------------------------------------------------
    csv_mode = False
    url_column = None
    if args.input:
        url_column = detect_csv_url_column(args.input)
        if url_column:
            csv_mode = True
            print(f"CSV mode detected — URL column: '{url_column}'")

    if args.url:
        # Single URL mode
        urls = [args.url]
        csv_mode = False
    elif csv_mode:
        # CSV mode — process rows and output enriched CSV
        rows = load_csv_rows(args.input, url_column)
        if args.start_from > 0:
            print(f"Skipping first {args.start_from} rows")
            rows = rows[args.start_from:]
        print(f"Found {len(rows)} rows to process")

        # Determine original fieldnames + new column
        with open(args.input, "r") as f:
            reader = csv.DictReader(f)
            original_fields = list(reader.fieldnames)
        output_fields = original_fields + ["BEAUTIFIED_URL"]

        output_csv_path = out_dir / "beautified_output.csv"
        stats = {"success": 0, "failed": 0, "skipped": 0}

        with open(output_csv_path, "w", newline="") as out_f:
            writer = csv.DictWriter(out_f, fieldnames=output_fields)
            writer.writeheader()

            for idx, row in enumerate(rows):
                url = (row.get(url_column) or "").strip()
                item_name = (row.get("ITEM_NAME") or row.get("item_name") or "")
                dd_sic = (row.get("DD_SIC") or row.get("dd_sic") or row.get("DD_SIC_V2") or "")
                label = f"{dd_sic} — {item_name[:50]}" if dd_sic else item_name[:60]

                print(f"\n[{idx + 1}/{len(rows)}] {label}")

                if not url or not url.startswith("http"):
                    print("  No valid URL, skipping")
                    row["BEAUTIFIED_URL"] = ""
                    writer.writerow(row)
                    stats["skipped"] += 1
                    continue

                if args.dry_run:
                    row["BEAUTIFIED_URL"] = ""
                    writer.writerow(row)
                    stats["skipped"] += 1
                    continue

                # Auto-detect model on first image
                if model == "auto" and not model_detected:
                    try:
                        test_bytes = download_image(url)
                        if test_bytes:
                            test_png = convert_to_png(test_bytes)
                            model = detect_model(client, test_png, args.prompt)
                            model_detected = True
                    except Exception:
                        model = "dall-e-2"
                        model_detected = True

                _, ibb_url, status = process_single_url(
                    url, client, model, args.prompt, imgbb_key,
                    out_dir, originals_dir, args.skip_upload
                )

                row["BEAUTIFIED_URL"] = ibb_url or ""
                writer.writerow(row)

                if status == "success":
                    stats["success"] += 1
                else:
                    stats["failed"] += 1

                # Rate limiting
                if idx < len(rows) - 1:
                    time.sleep(2)

        # Summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"  Model:     {model}")
        print(f"  Total:     {len(rows)}")
        print(f"  Success:   {stats['success']}")
        print(f"  Failed:    {stats['failed']}")
        print(f"  Skipped:   {stats['skipped']}")
        print(f"\n  Output CSV: {output_csv_path}")
        return

    else:
        # Plain URL list mode
        urls = load_urls_from_txt(args.input)

    # -----------------------------------------------------------------------
    # URL list mode (txt or single --url)
    # -----------------------------------------------------------------------
    if args.start_from > 0:
        print(f"Skipping first {args.start_from} URLs")
        urls = urls[args.start_from:]

    print(f"Found {len(urls)} image(s) to process")
    results = []

    for idx, url in enumerate(urls):
        print(f"\n[{idx + 1}/{len(urls)}] Processing: {url[:80]}...")

        if args.dry_run:
            filename = url_to_filename(url)
            image_bytes = download_image(url)
            if image_bytes:
                orig_path = originals_dir / filename
                with open(orig_path, "wb") as f:
                    f.write(image_bytes)
                results.append({"url": url, "status": "dry_run", "original": str(orig_path)})
            else:
                results.append({"url": url, "status": "download_failed"})
            continue

        # Auto-detect model on first image
        if model == "auto" and not model_detected:
            try:
                test_bytes = download_image(url)
                if test_bytes:
                    test_png = convert_to_png(test_bytes)
                    model = detect_model(client, test_png, args.prompt)
                    model_detected = True
            except Exception:
                model = "dall-e-2"
                model_detected = True

        enhanced_bytes, ibb_url, status = process_single_url(
            url, client, model, args.prompt, imgbb_key,
            out_dir, originals_dir, args.skip_upload
        )

        result_entry = {"url": url, "status": status, "model": model}
        if ibb_url:
            result_entry["imgbb_url"] = ibb_url
        results.append(result_entry)

        if idx < len(urls) - 1:
            time.sleep(2)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    success = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if "failed" in r["status"])
    uploaded = sum(1 for r in results if r.get("imgbb_url"))
    print(f"  Model:     {model}")
    print(f"  Total:     {len(results)}")
    print(f"  Success:   {success}")
    print(f"  Uploaded:  {uploaded}")
    print(f"  Failed:    {failed}")

    ibb_links = [(r.get("url", ""), r.get("imgbb_url", "")) for r in results if r.get("imgbb_url")]
    if ibb_links:
        print("\n" + "=" * 60)
        print("IMGBB LINKS")
        print("=" * 60)
        for original_url, ibb_url in ibb_links:
            short_name = url_to_filename(original_url)
            print(f"  {short_name}: {ibb_url}")

    log_path = out_dir / "results.json"
    with open(log_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Log saved: {log_path}")


if __name__ == "__main__":
    main()
