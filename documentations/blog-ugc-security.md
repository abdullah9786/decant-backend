# Blog UGC, HTML import, and CSP

## Threat model

- Community posts use **Editor.js JSON** only; text fields are normalized server-side with `nh3.clean_text` before storage.
- **Admin HTML import** (`admin_html`) is treated as hostile: `nh3.clean()` on every write and again on public read; storefront runs **DOMPurify** in `BlogHtmlBody` before `dangerouslySetInnerHTML`.
- **Reserved slugs** (`write`, `me`, `admin`, `rss.xml`, `guidelines`, …) block route takeover.
- **Published slugs** are unique via partial Mongo index on `status: published`.

## CSP (`decant-user/src/middleware.ts`)

- Applied under `/blog/*`.
- `frame-src` allows **YouTube** only for embed blocks; arbitrary iframes are stripped in HTML mode.
- `script-src` includes `'unsafe-inline'` because Next.js hydration uses inline scripts; tighten with nonces when feasible.

## Operational limits

- Admin HTML is capped at **800KB** before sanitization (`html_sanitize.MAX_ADMIN_HTML_BYTES`).
- Revalidation paths: `/blog`, `/blog/{slug}`, `/sitemap.xml` via `revalidate_blog_post`.
