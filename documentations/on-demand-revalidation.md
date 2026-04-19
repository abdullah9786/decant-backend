# On-Demand ISR Revalidation

## Problem

Vercel's free tier has limited Fluid Active CPU and Function Invocations. Time-based ISR (`revalidate: 120`) causes pages to re-render on a schedule even when nothing has changed, wasting resources.

## Current State (Time-Based ISR)

All server-rendered pages use `next: { revalidate: 600 }` (10 minutes). This means every 10 minutes, the next visitor triggers a background re-render — regardless of whether any data changed.

## Proposed Solution: On-Demand Revalidation

Instead of time-based, pages stay cached indefinitely and only re-render when the backend explicitly tells the frontend to do so (after an admin action).

---

## Implementation Steps

### Step 1: Environment Variables

Add to both backend `.env` and Vercel frontend environment variables:

```
REVALIDATE_SECRET=<generate-a-random-string>
FRONTEND_URL=https://decume.in
```

### Step 2: Next.js Revalidation API Route

Create `frontend-user/src/app/api/revalidate/route.ts`:

```typescript
import { revalidatePath } from 'next/cache';
import { NextRequest, NextResponse } from 'next/server';

export async function POST(request: NextRequest) {
  const secret = request.headers.get('x-revalidate-secret');
  if (secret !== process.env.REVALIDATE_SECRET) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const { paths } = await request.json();

  for (const path of paths) {
    revalidatePath(path);
  }

  return NextResponse.json({ revalidated: true, paths });
}
```

### Step 3: Backend Revalidation Utility

Create `backend/app/utils/revalidate.py`:

```python
import httpx
import os

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://decume.in")
REVALIDATE_SECRET = os.getenv("REVALIDATE_SECRET", "")

async def revalidate_paths(paths: list[str]):
    """Fire-and-forget revalidation request to Next.js frontend."""
    if not REVALIDATE_SECRET or not FRONTEND_URL:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{FRONTEND_URL}/api/revalidate",
                json={"paths": paths},
                headers={"x-revalidate-secret": REVALIDATE_SECRET},
                timeout=5.0,
            )
    except Exception:
        pass  # don't block admin actions if revalidation fails
```

### Step 4: Call from Routers

Add revalidation calls after create/update/delete operations in each router.

**Example — Product Router:**

```python
from app.utils.revalidate import revalidate_paths

@router.put("/{id}")
async def update_product(id: str, product_in: ProductUpdate, ...):
    updated = await product_service.update(id, product_in)
    await revalidate_paths(["/", "/products", f"/products/{id}"])
    return updated

@router.post("")
async def create_product(product_in: ProductCreate, ...):
    created = await product_service.create(product_in)
    await revalidate_paths(["/", "/products"])
    return created

@router.delete("/{id}")
async def delete_product(id: str, ...):
    await product_service.delete(id)
    await revalidate_paths(["/", "/products"])
    return None
```

### Step 5: Update Fetch Revalidation Times

Change all `revalidate` values in the frontend to `86400` (24 hours) as a fallback safety net:

```typescript
const res = await fetch(`${API_URL}/products`, {
  next: { revalidate: 86400 },
});
```

---

## Revalidation Path Map

| Admin Action | Paths to Revalidate |
|---|---|
| Create/update/delete **product** | `/`, `/products`, `/products/{id}` |
| Create/update/delete **category** | `/categories`, `/categories/{slug}` |
| Create/update/delete **fragrance family** | `/`, `/products`, `/families` |
| Create/update/delete **gift box** | `/gift-boxes`, `/gift-boxes/{id}` |
| Create/update/delete **bottle** | `/bottles`, `/products` |
| Create/update/delete **brand** | `/brands`, `/products` |
| Create/update/delete **influencer** | `/creators`, `/{username}` |
| Update **order status** | (no user-facing cached page affected) |

---

## Downsides & Considerations

1. **More code to maintain** — every new admin endpoint needs the right `revalidate_paths()` call. Easy to forget.
2. **Direct DB edits won't trigger revalidation** — only admin panel actions do. The 24h fallback covers this.
3. **Localhost development** — skip revalidation in dev by leaving `REVALIDATE_SECRET` empty locally.
4. **Bulk operations** — drag-and-drop reordering fires multiple revalidation calls. Consider debouncing or batching.
5. **Race conditions** — rapid successive edits may briefly show stale data. Self-correcting.

## When to Implement

- Deploy with the current 600s time-based approach first
- Monitor Vercel usage for 1-2 weeks
- Only implement on-demand revalidation if still hitting Vercel limits
- The 600s approach should reduce CPU usage by ~3-5x compared to the original 60-120s values

## Dependencies

- `httpx` — async HTTP client for Python (add to `requirements.txt` if not present)
