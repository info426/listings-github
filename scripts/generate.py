#!/usr/bin/env python3
"""
Generate SEO product listings using Claude API and publish them as JSON drafts.
Usage: python scripts/generate.py <brand>
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import requests
from bs4 import BeautifulSoup

SHOPIFY_DOMAIN = os.environ["SHOPIFY_STORE_DOMAIN"]
SHOPIFY_TOKEN = os.environ["SHOPIFY_ACCESS_TOKEN"]
SHOPIFY_API_VERSION = "2024-10"

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

TOOLS = [
    {
        "name": "web_fetch",
        "description": (
            "Fetches the full text content of a URL. Use token_limit between 5000 and 10000. "
            "If content is truncated, try a more specific URL or search query."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL to fetch"},
                "token_limit": {
                    "type": "integer",
                    "description": "Approximate token limit for response (4 chars ≈ 1 token)",
                    "default": 8000,
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "web_search",
        "description": "Searches the web and returns a list of results with title, URL, and snippet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
]


def web_fetch(url: str, token_limit: int = 8000) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "es-ES,es;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        char_limit = token_limit * 4
        if len(text) > char_limit:
            text = text[:char_limit] + "\n\n[CONTENIDO TRUNCADO — usar URL más específica]"
        return text
    except Exception as exc:
        return f"Error al obtener {url}: {exc}"


def web_search(query: str, max_results: int = 5) -> str:
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return json.dumps(results, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"Error en búsqueda: {exc}"


def execute_tool(name: str, tool_input: dict) -> str:
    if name == "web_fetch":
        return web_fetch(tool_input["url"], tool_input.get("token_limit", 8000))
    if name == "web_search":
        return web_search(tool_input["query"], tool_input.get("max_results", 5))
    return f"Herramienta desconocida: {name}"


def get_shopify_products(vendor: str) -> list:
    url = f"https://{SHOPIFY_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}/products.json"
    headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN}
    params = {
        "vendor": vendor,
        "limit": 250,
        "fields": "id,title,handle,vendor,variants,product_type,tags",
    }

    products = []
    while url:
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        products.extend(resp.json()["products"])

        next_url = None
        link_header = resp.headers.get("Link", "")
        if 'rel="next"' in link_header:
            for part in link_header.split(","):
                if 'rel="next"' in part:
                    next_url = part.split(";")[0].strip().strip("<>")
        url = next_url
        params = None

    return products


def build_user_message(product: dict, prompt_template: str) -> str:
    variants = [
        f"{v.get('title', '')} — {v.get('price', '')} €"
        for v in product.get("variants", [])
    ]
    return f"""{prompt_template}

---

## Producto a procesar

- **Título en Shopify:** {product["title"]}
- **Marca / Fabricante:** {product.get("vendor", "")}
- **Tipo de producto:** {product.get("product_type", "")}
- **Variantes disponibles:** {", ".join(variants) if variants else "—"}
- **Tags:** {product.get("tags", "—")}

Busca este producto en la web oficial del fabricante, extrae TODA la información técnica \
y genera la ficha SEO completa siguiendo todas las reglas del prompt anterior.

## Formato de respuesta OBLIGATORIO

Responde ÚNICAMENTE con un bloque JSON válido. Sin texto antes ni después. Estructura exacta:

```json
{{
  "meta_title": "...",
  "meta_description": "...",
  "slug": "...",
  "body_html": "...",
  "missing_data": []
}}
```
"""


def parse_json_from_response(text: str) -> dict:
    # Try JSON inside code fences first
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    # Try bare JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"No se encontró JSON válido en la respuesta. Inicio: {text[:300]}")


def generate_listing(product: dict, prompt_template: str) -> dict:
    messages = [{"role": "user", "content": build_user_message(product, prompt_template)}]

    for _ in range(25):
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=8192,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input)
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": result}
                    )
            messages.append({"role": "user", "content": tool_results})

        elif response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    return parse_json_from_response(block.text)
            break

    raise RuntimeError("Claude no devolvió una respuesta final después de 25 iteraciones")


def main() -> None:
    brand = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BRAND", "")).strip()
    if not brand:
        print("Uso: python scripts/generate.py <marca>", file=sys.stderr)
        sys.exit(1)

    prompt_path = Path(__file__).parent.parent / "prompts" / "seo-prompt-v12.md"
    prompt_template = prompt_path.read_text(encoding="utf-8")

    print(f"Obteniendo productos de Shopify para la marca: {brand}")
    products = get_shopify_products(brand)
    print(f"Encontrados {len(products)} productos")

    if not products:
        print("No se encontraron productos. Verifica el nombre exacto del vendor en Shopify.")
        sys.exit(0)

    brand_slug = brand.lower().replace(" ", "-")
    output_dir = Path(__file__).parent.parent / "listings" / "pending" / brand_slug
    output_dir.mkdir(parents=True, exist_ok=True)

    errors = []
    for i, product in enumerate(products, start=1):
        handle = product["handle"]
        output_file = output_dir / f"{handle}.json"

        if output_file.exists():
            print(f"[{i}/{len(products)}] Saltando {handle} (ya existe)")
            continue

        print(f"[{i}/{len(products)}] Generando: {product['title']}")

        try:
            listing = generate_listing(product, prompt_template)

            result = {
                "shopify_product_id": product["id"],
                "shopify_handle": handle,
                "product_title": product["title"],
                "brand": product.get("vendor", brand),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "prompt_version": "v12",
                "seo": {
                    "title": listing["meta_title"],
                    "description": listing["meta_description"],
                    "handle": listing["slug"],
                },
                "body_html": listing["body_html"],
                "missing_data": listing.get("missing_data", []),
            }

            output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  ✓ Guardado en {output_file.relative_to(Path(__file__).parent.parent)}")

        except Exception as exc:
            msg = str(exc)
            print(f"  ✗ Error: {msg}")
            errors.append({"handle": handle, "title": product["title"], "error": msg})
            error_file = output_dir / f"{handle}.error.json"
            error_file.write_text(
                json.dumps(
                    {"shopify_product_id": product["id"], "product_title": product["title"], "error": msg},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        if i < len(products):
            time.sleep(2)

    print(f"\nCompletado: {len(products) - len(errors)} OK, {len(errors)} errores")
    if errors:
        for e in errors:
            print(f"  ✗ {e['handle']}: {e['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
