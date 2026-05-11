# ShopyPet — Automatización de listings SEO con GitHub Actions

Genera y publica automáticamente fichas de producto SEO para Shopify usando Claude AI y el [Prompt SEO v12](prompts/seo-prompt-v12.md).

---

## Flujo de trabajo

```
[Action 1: Generate]          [Revisión humana]        [Action 2: Publish]
Shopify API → Claude AI  →  listings/pending/  →  revisas/editas  →  Shopify API
                                    ↓ aprobado ↓
                             listings/published/
```

### Paso 1 — Generar listings

1. Ve a **Actions → 1 · Generate Listings → Run workflow**
2. Introduce el nombre exacto del vendor en Shopify (p.ej. `Virbac`)
3. El Action:
   - Obtiene todos los productos de esa marca desde Shopify
   - Para cada producto llama a Claude (claude-opus-4-7) con el Prompt SEO v12
   - Claude busca en la web del fabricante, extrae datos y genera el HTML
   - Guarda el resultado en `listings/pending/<marca>/<handle>.json`
   - Hace commit automático de los ficheros

### Paso 2 — Revisar

Navega a `listings/pending/<marca>/` en GitHub y revisa cada JSON. Puedes editar directamente en la web o hacer `git pull` y editar localmente.

Cada fichero tiene esta estructura:

```json
{
  "shopify_product_id": 123456,
  "shopify_handle": "pienso-digestivo-virbac-hpm",
  "product_title": "Virbac HPM Feline G1",
  "brand": "Virbac",
  "generated_at": "2026-05-11T10:30:00Z",
  "prompt_version": "v12",
  "seo": {
    "title": "Pienso digestivo Virbac HPM Feline G1 para gatos | ShopyPet",
    "description": "Mejora la digestión de tu gato con Virbac HPM Feline G1. ¡Consíguelo ya!",
    "handle": "pienso-digestivo-virbac-hpm-feline-g1-gatos"
  },
  "body_html": "<h2>...</h2>...",
  "missing_data": []
}
```

El campo `missing_data` lista los datos que Claude no encontró en la web del fabricante.

### Paso 3 — Publicar en Shopify

1. Ve a **Actions → 2 · Publish to Shopify → Run workflow**
2. Introduce la marca (o deja vacío para publicar todas las pendientes)
3. El Action actualiza en Shopify: `body_html`, `handle`, meta título y meta descripción
4. Mueve los ficheros de `listings/pending/` a `listings/published/` como registro

---

## Configuración inicial

### Secrets de GitHub

Ve a **Settings → Secrets and variables → Actions** y añade:

| Secret | Descripción |
|--------|-------------|
| `ANTHROPIC_API_KEY` | API key de Anthropic (platform.anthropic.com) |
| `SHOPIFY_STORE_DOMAIN` | Dominio de tu tienda, p.ej. `shopypet.myshopify.com` |
| `SHOPIFY_ACCESS_TOKEN` | Token de Admin API de Shopify (permisos: `read_products`, `write_products`) |

### Permisos del token de Shopify

En Shopify Admin → Apps → Develop apps → crea una app con:
- `read_products`
- `write_products`

---

## Estructura del repositorio

```
.
├── .github/workflows/
│   ├── generate-listings.yml   # Action 1: genera drafts
│   └── publish-to-shopify.yml  # Action 2: publica en Shopify
├── listings/
│   ├── pending/                # JSONs pendientes de revisión
│   │   └── virbac/
│   │       └── *.json
│   └── published/              # Registro de los publicados
│       └── virbac/
│           └── *.json
├── prompts/
│   └── seo-prompt-v12.md       # Prompt SEO (actualiza aquí la versión)
├── scripts/
│   ├── generate.py             # Lógica de generación
│   └── publish.py              # Lógica de publicación
└── requirements.txt
```

---

## Actualizar el prompt SEO

Solo edita `prompts/seo-prompt-v12.md` y haz commit. La siguiente ejecución del Action usará la versión actualizada.
