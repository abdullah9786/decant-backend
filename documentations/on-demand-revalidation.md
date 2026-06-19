# On-Demand ISR Revalidation

## Problem

Time-based ISR (`revalidate: 600` or `60`) causes pages to re-render on a schedule even when nothing has changed, wasting Vercel Fluid CPU and ISR writes.

## Solution (implemented)

Pages stay cached for **24 hours** (`86400` seconds) as a safety net. The backend calls `/api/revalidate` after admin mutations so the storefront updates immediately — without scheduled churn.

---

## Environment Variables

Set on **backend** (`.env`) and **Vercel** (`decant-user`):

```
FRONTEND_URL=https://decume.in
REVALIDATE_SECRET=<shared-random-secret>
```

If either is missing locally, revalidation calls no-op (safe for dev).

---

## Frontend

### Cache config

- `decant-user/src/lib/cacheConfig.ts` — `CACHE_REVALIDATE_SECONDS` (86400 prod, 60 dev)
- `decant-user/src/lib/cacheTags.ts` — `product-reviews:{id}`, `daily-deal`

### Revalidation API

`decant-user/src/app/api/revalidate/route.ts` — accepts `{ paths?, tags? }` with header `x-revalidate-secret`.

### SEO preserved

- Product `generateMetadata`, JSON-LD (Product, Breadcrumb, **reviews/ratings**), H1, and related products remain **server-rendered**.
- Review changes still revalidate **paths + tags** so Google sees fresh structured data.

---

## Backend

Utility: `app/utils/revalidate.py`

| Helper | When |
|--------|------|
| `revalidate_product(id, slug?)` | Product create/update/delete |
| `revalidate_products_catalog()` | Bulk chip updates |
| `revalidate_category(slug?)` | Category CRUD |
| `revalidate_gift_box(id)` | Gift box CRUD |
| `revalidate_bottles()` | Bottle CRUD |
| `revalidate_brands()` | Brand CRUD |
| `revalidate_fragrance_families()` | Fragrance family CRUD |
| `revalidate_influencer(username?)` | Influencer / storefront CRUD |
| `revalidate_daily_deal(product_ids?, extra_paths=…)` | Offer CRUD (daily deal `daily-deal` tag + paths incl. slug PDPs) |
| `revalidate_product_reviews(id, slug?)` | Review create/update/delete/bulk |

---

## Revalidation path map

| Admin action | Paths / tags |
|---|---|
| Product CRUD | `/`, `/products`, `/sitemap.xml`, `/products/{id}`, `/products/{slug}` |
| Bulk chips | `/`, `/products`, `/sitemap.xml` |
| Category CRUD | `/`, `/categories`, `/categories/{slug}` |
| Gift box CRUD | `/gift-boxes`, `/gift-boxes/{id}` |
| Bottle CRUD | `/bottles`, `/products` |
| Brand CRUD | `/brands`, `/products` |
| Fragrance family CRUD | `/`, `/products`, `/families` |
| Influencer / storefront | `/creators`, `/sitemap.xml`, `/{username}` |
| Daily deal offer | tag `daily-deal`, `/`, `/deals/today`, `/products`, `/products/{dealProductId}`, `/products/{slug}` for each deal product |
| Review change | tag `product-reviews:{id}`, `/products/{id}`, `/products/{slug}` |

---

## Considerations

1. **Direct DB edits** won't trigger revalidation — only admin panel actions. The 24h fallback covers this.
2. **Bulk operations** may fire multiple revalidation calls; acceptable at current scale.
3. **Daily deal midnight rollover** — cached until next visit after 24h unless admin toggles an offer; `ActiveDealProvider` still refetches client-side for live UX.

## Dependencies

- `httpx` — async HTTP client for revalidation webhooks
