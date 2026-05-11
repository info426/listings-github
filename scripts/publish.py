#!/usr/bin/env python3
"""
Publish reviewed SEO listings from listings/pending/ to Shopify.
Moves processed files to listings/published/.
Usage: python scripts/publish.py [brand]  (omit brand to publish all pending)
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

import requests

SHOPIFY_DOMAIN = os.environ["SHOPIFY_STORE_DOMAIN"]
SHOPIFY_TOKEN = os.environ["SHOPIFY_ACCESS_TOKEN"]
SHOPIFY_API_VERSION = "2024-10"


def update_shopify_product(product_id: int, body_html: str, seo_title: str, seo_description: str, handle: str) -> dict:
    url = f"https://{SHOPIFY_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}/products/{product_id}.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_TOKEN,
        "Content-Type": "application/json",
    }
    payload = {
        "product": {
            "id": product_id,
            "body_html": body_html,
            "handle": handle,
            "metafields_global_title_tag": seo_title,
            "metafields_global_description_tag": seo_description,
        }
    }
    resp = requests.put(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def publish_brand(brand_dir: Path) -> tuple[int, int]:
    published_dir = brand_dir.parents[1] / "published" / brand_dir.name
    published_dir.mkdir(parents=True, exist_ok=True)

    json_files = sorted(f for f in brand_dir.glob("*.json") if not f.name.endswith(".error.json"))
    if not json_files:
        print(f"  Sin ficheros pendientes en {brand_dir.name}")
        return 0, 0

    ok = errors = 0
    for i, json_file in enumerate(json_files, start=1):
        listing = json.loads(json_file.read_text(encoding="utf-8"))
        title = listing.get("product_title", json_file.stem)
        product_id = listing["shopify_product_id"]

        print(f"  [{i}/{len(json_files)}] {title}")
        try:
            update_shopify_product(
                product_id=product_id,
                body_html=listing["body_html"],
                seo_title=listing["seo"]["title"],
                seo_description=listing["seo"]["description"],
                handle=listing["seo"]["handle"],
            )
            shutil.move(str(json_file), str(published_dir / json_file.name))
            print(f"    ✓ Publicado y movido a published/{brand_dir.name}/")
            ok += 1
        except Exception as exc:
            print(f"    ✗ Error: {exc}")
            errors += 1

        if i < len(json_files):
            time.sleep(0.5)  # Shopify rate limit: 2 req/s on standard plan

    return ok, errors


def main() -> None:
    brand_arg = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BRAND", "")).strip()

    pending_root = Path(__file__).parent.parent / "listings" / "pending"

    if brand_arg:
        brand_slug = brand_arg.lower().replace(" ", "-")
        brand_dirs = [pending_root / brand_slug]
        if not brand_dirs[0].exists():
            print(f"No hay listings pendientes para: {brand_arg}")
            sys.exit(0)
    else:
        brand_dirs = sorted(d for d in pending_root.iterdir() if d.is_dir()) if pending_root.exists() else []

    if not brand_dirs:
        print("No hay listings pendientes.")
        sys.exit(0)

    total_ok = total_errors = 0
    for brand_dir in brand_dirs:
        print(f"\nPublicando marca: {brand_dir.name}")
        ok, errors = publish_brand(brand_dir)
        total_ok += ok
        total_errors += errors

    print(f"\nResumen: {total_ok} publicados, {total_errors} errores")
    if total_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
