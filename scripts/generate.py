#!/usr/bin/env python3
"""
Generate SEO product listings using GitHub Models (free via GITHUB_TOKEN).
Usage: python scripts/generate.py <brand>
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from openai import OpenAI

SHOPIFY_DOMAIN = os.environ["SHOPIFY_STORE_DOMAIN"]
SHOPIFY_TOKEN = os.environ["SHOPIFY_ACCESS_TOKEN"]
SHOPIFY_API_VERSION = "2024-10"

# GitHub Models — free for public repos, uses GITHUB_TOKEN automatically
# Endpoint updated May 2025: https://github.blog/changelog/2025-05-15-github-models-api-now-available/
client = OpenAI(
    base_url="https://models.github.ai/inference",
    api_key=os.environ["GITHUB_TOKEN"],
)
MODEL = os.environ.get("MODEL", "openai/gpt-4.1")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Fetches the full text content of a URL. "
                "Use token_limit between 5000 and 10000. "
                "If content is truncated, try a more specific URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to fetch"},
                    "token_limit": {
                        "type": "integer",
                        "description": "Approx token limit for response (4 chars ≈ 1 token)",
                        "default": 8000,
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Searches the web and returns results with title, URL and snippet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Web tools
# ---------------------------------------------------------------------------

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


def execute_tool(name: str, arguments: dict) -> str:
    if name == "web_fetch":
        return web_fetch(arguments["url"], arguments.get("token_limit", 8000))
    if name == "web_search":
        return web_search(arguments["query"], arguments.get("max_results", 5))
    return f"Herramienta desconocida: {name}"


# ---------------------------------------------------------------------------
# Shopify helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Listing generation — agentic loop with GitHub Models
# ---------------------------------------------------------------------------

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
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"No se encontró JSON válido. Inicio: {text[:300]}")


def call_with_retry(messages: list, attempt: int = 0) -> object:
    """Call GitHub Models API with exponential backoff on rate limit errors."""
    try:
        return client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=8192,
        )
    except Exception as exc:
        if attempt < 4 and ("rate" in str(exc).lower() or "429" in str(exc)):
            wait = 2 ** (attempt + 1)
            print(f"    Rate limit — esperando {wait}s...")
            time.sleep(wait)
            return call_with_retry(messages, attempt + 1)
        raise


def generate_listing(product: dict, prompt_template: str) -> dict:
    messages = [
        {
            "role": "system",
            "content": (
                "Eres un especialista SEO para ShopyPet (shopypet.eu). "
                "Sigues el prompt al pie de la letra y devuelves SOLO JSON válido."
            ),
        },
        {"role": "user", "content": build_user_message(product, prompt_template)},
    ]

    for _ in range(25):
        response = call_with_retry(messages)
        choice = response.choices[0]

        if choice.finish_reason == "tool_calls":
            # Append assistant message with tool calls
            messages.append({"role": "assistant", "content": choice.message.content, "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in choice.message.tool_calls
            ]})

            # Execute each tool and append results
            for tc in choice.message.tool_calls:
                args = json.loads(tc.function.arguments)
                result = execute_tool(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        elif choice.finish_reason == "stop":
            return parse_json_from_response(choice.message.content)

    raise RuntimeError("No se obtuvo respuesta final tras 25 iteraciones")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    brand = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BRAND", "")).strip()
    if not brand:
        print("Uso: python scripts/generate.py <marca>", file=sys.stderr)
        sys.exit(1)

    prompt_path = Path(__file__).parent.parent / "prompts" / "seo-prompt-v12.md"
    prompt_template = prompt_path.read_text(encoding="utf-8")

    print(f"Modelo: {MODEL}")
    print(f"Obteniendo productos de Shopify para: {brand}")
    products = get_shopify_products(brand)
    print(f"Encontrados {len(products)} productos")

    if not products:
        print("No se encontraron productos. Verifica el vendor exacto en Shopify.")
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
                "model": MODEL,
                "seo": {
                    "title": listing["meta_title"],
                    "description": listing["meta_description"],
                    "handle": listing["slug"],
                },
                "body_html": listing["body_html"],
                "missing_data": listing.get("missing_data", []),
            }

            output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  ✓ {output_file.relative_to(Path(__file__).parent.parent)}")

        except Exception as exc:
            msg = str(exc)
            print(f"  ✗ Error: {msg}")
            errors.append({"handle": handle, "title": product["title"], "error": msg})
            (output_dir / f"{handle}.error.json").write_text(
                json.dumps({"shopify_product_id": product["id"], "product_title": product["title"], "error": msg},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        if i < len(products):
            time.sleep(3)  # Avoid hammering GitHub Models rate limits

    print(f"\nCompletado: {len(products) - len(errors)} OK · {len(errors)} errores")
    if errors:
        for e in errors:
            print(f"  ✗ {e['handle']}: {e['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
