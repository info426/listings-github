#!/usr/bin/env python3
"""
Generate SEO product listings using GitHub Models (free via GITHUB_TOKEN).

Two-phase approach:
  Phase 1 — Research: model uses web tools to collect ALL manufacturer data
  Phase 2 — Generate: model produces the SEO HTML from the collected data

Usage: python scripts/generate.py <brand>
       PRODUCT_TITLE="..." python scripts/generate.py <brand>   # manual/test mode
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

SHOPIFY_DOMAIN = os.environ.get("SHOPIFY_STORE_DOMAIN", "")
SHOPIFY_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
SHOPIFY_API_VERSION = "2024-10"

# GitHub Models — free for public repos, uses GITHUB_TOKEN automatically
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
                "If a section is truncated or missing, call again with a more specific subpage URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "token_limit": {"type": "integer", "default": 10000},
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
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Web tools implementation
# ---------------------------------------------------------------------------

def web_fetch(url: str, token_limit: int = 10000) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
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
            text = text[:char_limit] + "\n\n[CONTENIDO TRUNCADO — llamar de nuevo con URL más específica]"
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
        return web_fetch(arguments["url"], arguments.get("token_limit", 10000))
    if name == "web_search":
        return web_search(arguments["query"], arguments.get("max_results", 5))
    return f"Herramienta desconocida: {name}"


# ---------------------------------------------------------------------------
# API call helpers
# ---------------------------------------------------------------------------

def api_call(messages: list, use_tools: bool = False, attempt: int = 0):
    try:
        kwargs = dict(model=MODEL, messages=messages, max_tokens=8192)
        if use_tools:
            kwargs["tools"] = TOOLS
            kwargs["tool_choice"] = "auto"
        return client.chat.completions.create(**kwargs)
    except Exception as exc:
        if attempt < 4 and ("rate" in str(exc).lower() or "429" in str(exc)):
            wait = 2 ** (attempt + 1)
            print(f"    Rate limit — esperando {wait}s...", flush=True)
            time.sleep(wait)
            return api_call(messages, use_tools, attempt + 1)
        raise


# ---------------------------------------------------------------------------
# Phase 1 — Research: collect ALL manufacturer data with tools
# ---------------------------------------------------------------------------

RESEARCH_SYSTEM = """\
Eres un investigador de productos. Tu ÚNICA tarea es recopilar datos técnicos \
de la web OFICIAL del fabricante. NO generes HTML. NO inventes datos.

PASOS OBLIGATORIOS que debes ejecutar en orden:

1. Llama a web_search("{titulo} {marca} sitio oficial") para localizar la URL exacta \
del producto en la web del fabricante (no retailers).
2. Llama a web_fetch(URL, token_limit=10000) sobre esa página del producto.
3. Comprueba si la página incluye: ingredientes, aditivos nutricionales, componentes \
analíticos y guía de alimentación. Si falta alguno, llama a web_fetch en las subpáginas \
correspondientes del fabricante hasta completarlos.
4. Llama a web_search UNA VEZ para identificar 5 preguntas reales de usuarios sobre \
este tipo de producto (foros, Yahoo Answers, Reddit, etc.).
5. Devuelve el JSON con TODOS los datos recogidos.\
"""


def build_research_message(product: dict) -> str:
    return f"""\
Producto a investigar:
- Título: {product["title"]}
- Marca: {product.get("vendor", "")}

Sigue los pasos del sistema y recopila TODOS los datos del fabricante.

Devuelve ÚNICAMENTE este JSON (sin texto antes ni después):

```json
{{
  "manufacturer_url": "URL exacta donde encontraste el producto",
  "product_name_official": "nombre oficial según el fabricante",
  "species": "especie destino (gato / perro / etc.)",
  "product_description": "descripción completa del fabricante",
  "product_claims": "todos los beneficios e indicaciones del fabricante",
  "ingredients": "lista completa de ingredientes tal como aparece en la web",
  "additives": "aditivos nutricionales completos tal como aparecen",
  "analytical_components": "componentes analíticos completos tal como aparecen",
  "feeding_guide": "guía de alimentación completa con tablas de peso",
  "format_presentation": "formato, tipo de envase, presentación",
  "weight_sizes": "pesos y tamaños disponibles",
  "other_technical_data": "cualquier otro dato técnico relevante",
  "faq_questions": [
    "pregunta real de usuario 1",
    "pregunta real de usuario 2",
    "pregunta real de usuario 3",
    "pregunta real de usuario 4",
    "pregunta real de usuario 5"
  ],
  "missing_data": ["datos no encontrados en la web oficial"]
}}
```\
"""


def research_product(product: dict) -> dict:
    """Phase 1: agentic loop with tools to collect manufacturer data."""
    messages = [
        {"role": "system", "content": RESEARCH_SYSTEM},
        {"role": "user", "content": build_research_message(product)},
    ]
    print("    [Fase 1] Investigando fabricante...", flush=True)

    for iteration in range(25):
        response = api_call(messages, use_tools=True)
        choice = response.choices[0]

        if choice.finish_reason == "tool_calls":
            tool_names = [tc.function.name for tc in choice.message.tool_calls]
            print(f"    [Fase 1] iter {iteration + 1}: {', '.join(tool_names)}", flush=True)

            messages.append({
                "role": "assistant",
                "content": choice.message.content,
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in choice.message.tool_calls
                ],
            })
            for tc in choice.message.tool_calls:
                result = execute_tool(tc.function.name, json.loads(tc.function.arguments))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        elif choice.finish_reason in ("stop", "length"):
            if choice.finish_reason == "length":
                print("    [Fase 1] AVISO: respuesta truncada", flush=True)
            return parse_json_from_response(choice.message.content or "")

    raise RuntimeError("Fase 1: sin respuesta JSON tras 25 iteraciones")


# ---------------------------------------------------------------------------
# Phase 2 — Generate: produce the full SEO HTML from collected data
# ---------------------------------------------------------------------------

GENERATE_SYSTEM = """\
Eres un especialista SEO para ShopyPet (shopypet.eu).
Recibes datos técnicos ya recopilados de la web del fabricante.
Tu tarea es generar la ficha HTML COMPLETA siguiendo el PROMPT SEO v12 al pie de la letra.

REGLAS QUE NO PUEDES SALTARTE:
- Genera TODAS las secciones H2 del esquema (sección 4) que tengan datos disponibles.
- Usa el HTML EXACTO de la sección 5: bullets con span verde, tablas con los estilos indicados.
- NO omitas ingredientes, aditivos ni componentes analíticos si están en los datos.
- La meta descripción NUNCA lleva datos técnicos (regla 8.2 — solo beneficio + especie + CTA).
- Verifica tu salida contra el checklist de la sección 9 antes de responder.
- Devuelve SOLO el JSON, sin texto antes ni después.\
"""


def build_generate_message(product: dict, research: dict, prompt_template: str) -> str:
    return f"""\
## DATOS RECOPILADOS DEL FABRICANTE

```json
{json.dumps(research, ensure_ascii=False, indent=2)}
```

---

## PROMPT SEO v12 — SIGUE TODAS LAS REGLAS

{prompt_template}

---

## Producto a fichar

- **Título en Shopify:** {product["title"]}
- **Marca:** {product.get("vendor", "")}

---

Genera la ficha SEO completa usando los datos del fabricante de arriba.
Devuelve SOLO este JSON:

```json
{{
  "meta_title": "...",
  "meta_description": "...",
  "slug": "...",
  "body_html": "...",
  "missing_data": []
}}
```\
"""


def generate_html(product: dict, research: dict, prompt_template: str) -> dict:
    """Phase 2: generate SEO HTML from research data (no tools)."""
    messages = [
        {"role": "system", "content": GENERATE_SYSTEM},
        {"role": "user", "content": build_generate_message(product, research, prompt_template)},
    ]
    print("    [Fase 2] Generando HTML SEO...", flush=True)

    response = api_call(messages, use_tools=False)
    choice = response.choices[0]
    text = choice.message.content or ""

    if choice.finish_reason == "length":
        print("    [Fase 2] Output truncado — continuando...", flush=True)
        messages.append({"role": "assistant", "content": text})
        messages.append({
            "role": "user",
            "content": (
                "El JSON fue truncado por el límite de tokens. "
                "Continúa exactamente desde donde se cortó, sin repetir nada anterior."
            ),
        })
        cont = api_call(messages, use_tools=False)
        text = text + (cont.choices[0].message.content or "")

    return parse_json_from_response(text)


# ---------------------------------------------------------------------------
# Main generation entry point
# ---------------------------------------------------------------------------

def generate_listing(product: dict, prompt_template: str) -> dict:
    """Two-phase: research then generate."""
    research = research_product(product)
    return generate_html(product, research, prompt_template)


# ---------------------------------------------------------------------------
# JSON parser
# ---------------------------------------------------------------------------

def parse_json_from_response(text: str) -> dict:
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"No se encontró JSON válido. Inicio: {text[:300]}")


# ---------------------------------------------------------------------------
# Shopify helpers
# ---------------------------------------------------------------------------

def get_shopify_products(vendor: str) -> list:
    url = f"https://{SHOPIFY_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}/products.json"
    headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN}
    params = {"vendor": vendor, "limit": 250,
               "fields": "id,title,handle,vendor,variants,product_type,tags"}

    products = []
    while url:
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        products.extend(resp.json()["products"])
        next_url = None
        for part in resp.headers.get("Link", "").split(","):
            if 'rel="next"' in part:
                next_url = part.split(";")[0].strip().strip("<>")
        url = next_url
        params = None

    return products


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", text.lower())
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:80]


def save_listing(product: dict, listing: dict, output_dir: Path, brand: str) -> Path:
    handle = product.get("handle") or slugify(product["title"])
    result = {
        "shopify_product_id": product.get("id"),
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
    output_file = output_dir / f"{handle}.json"
    output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_file


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    brand = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BRAND", "")).strip()
    product_title = os.environ.get("PRODUCT_TITLE", "").strip()

    if not brand:
        print("Uso: python scripts/generate.py <marca>", file=sys.stderr)
        sys.exit(1)

    prompt_path = Path(__file__).parent.parent / "prompts" / "seo-prompt-v12.md"
    prompt_template = prompt_path.read_text(encoding="utf-8")
    brand_slug = brand.lower().replace(" ", "-")
    output_dir = Path(__file__).parent.parent / "listings" / "pending" / brand_slug
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Modelo: {MODEL}", flush=True)

    # --- Manual / test mode ---
    if product_title:
        print(f"Modo manual — producto: {product_title}", flush=True)
        product = {"title": product_title, "vendor": brand, "product_type": "", "variants": [], "tags": ""}
        listing = generate_listing(product, prompt_template)
        output_file = save_listing(product, listing, output_dir, brand)
        print(f"  ✓ {output_file.relative_to(Path(__file__).parent.parent)}")
        return

    # --- Normal mode: fetch all vendor products from Shopify ---
    print(f"Obteniendo productos de Shopify para: {brand}", flush=True)
    products = get_shopify_products(brand)
    print(f"Encontrados {len(products)} productos", flush=True)

    if not products:
        print("No se encontraron productos. Verifica el vendor exacto en Shopify.")
        sys.exit(0)

    errors = []
    for i, product in enumerate(products, start=1):
        handle = product["handle"]
        output_file = output_dir / f"{handle}.json"

        if output_file.exists():
            print(f"[{i}/{len(products)}] Saltando {handle} (ya existe)")
            continue

        print(f"[{i}/{len(products)}] Generando: {product['title']}", flush=True)

        try:
            listing = generate_listing(product, prompt_template)
            out = save_listing(product, listing, output_dir, brand)
            print(f"  ✓ {out.relative_to(Path(__file__).parent.parent)}")
        except Exception as exc:
            msg = str(exc)
            print(f"  ✗ Error: {msg}")
            errors.append({"handle": handle, "title": product["title"], "error": msg})
            (output_dir / f"{handle}.error.json").write_text(
                json.dumps({"shopify_product_id": product["id"],
                            "product_title": product["title"], "error": msg},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        if i < len(products):
            time.sleep(3)

    print(f"\nCompletado: {len(products) - len(errors)} OK · {len(errors)} errores")
    if errors:
        for e in errors:
            print(f"  ✗ {e['handle']}: {e['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
