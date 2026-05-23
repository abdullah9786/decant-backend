"""Pricing helpers for daily-deal annotation and checkout re-validation.

Single source of truth for "what is variant X actually priced at right now?".
Every surface that needs to show or accept a price must go through this so
the discount math stays consistent and tamper-proof.
"""

from copy import deepcopy
from typing import Any, Optional


def _variant_matches_apply_to(variant: dict, apply_to: str) -> bool:
    """Does this variant get discounted under the deal's `apply_to` rule?

    `apply_to` values:
      - "all":    every variant of a deal product is discounted.
      - "decant": only non-pack variants.
      - "pack":   only sealed-bottle pack variants.
    """
    is_pack = bool(variant.get("is_pack"))
    if apply_to == "decant":
        return not is_pack
    if apply_to == "pack":
        return is_pack
    return True


def _is_product_on_deal(product: dict, deal: Optional[dict]) -> bool:
    if not deal:
        return False
    config = deal.get("config") or {}
    product_ids = config.get("product_ids") or []
    pid = str(product.get("_id") or product.get("id") or "")
    if not pid:
        return False
    return pid in [str(p) for p in product_ids]


def apply_daily_deal(product: dict, deal: Optional[dict]) -> dict:
    """Return a copy of `product` with each variant annotated.

    Every variant gets `original_price`, `sale_price`, and `discount_percent`
    fields. For products that aren't on the deal (or variants that don't match
    `apply_to`), `sale_price == original_price` and `discount_percent == 0` so
    the frontend can treat the fields uniformly without branching.

    The base `variant.price` field is left intact for backwards compatibility
    with consumers that haven't been updated yet.
    """
    if not isinstance(product, dict):
        return product

    out = deepcopy(product)
    variants = out.get("variants") or []
    on_deal = _is_product_on_deal(out, deal)
    config = (deal or {}).get("config") or {}
    discount_percent = int(config.get("discount_percent") or 0) if on_deal else 0
    apply_to = (config.get("apply_to") or "all") if on_deal else "all"
    deal_id = str((deal or {}).get("_id") or "") if on_deal else None

    for v in variants:
        original = float(v.get("price") or 0)
        v["original_price"] = original
        if (
            on_deal
            and discount_percent > 0
            and _variant_matches_apply_to(v, apply_to)
        ):
            sale = round(original * (100 - discount_percent) / 100.0, 2)
            v["sale_price"] = sale
            v["discount_percent"] = discount_percent
            v["deal_id"] = deal_id
        else:
            v["sale_price"] = original
            v["discount_percent"] = 0
            v["deal_id"] = None

    return out


def compute_line_unit_price(
    product: dict,
    *,
    size_ml: int,
    is_pack: bool,
    deal: Optional[dict],
) -> Optional[float]:
    """Server-side derivation of what a single unit should cost right now.

    Returns `None` if the variant can't be located on the product (caller
    should reject the line). The returned value is the post-discount price
    when the deal applies, otherwise the variant's base price.
    """
    if not isinstance(product, dict):
        return None
    annotated = apply_daily_deal(product, deal)
    for v in annotated.get("variants") or []:
        try:
            if int(v.get("size_ml", -1)) == int(size_ml) and bool(v.get("is_pack")) == bool(is_pack):
                return float(v.get("sale_price"))
        except (TypeError, ValueError):
            continue
    return None


def find_deal_lines(items: list[dict], deal: Optional[dict]) -> list[dict]:
    """Return the subset of `items` whose product_id is part of the deal.

    Used by the coupon block and the order validation re-check.
    """
    if not deal:
        return []
    config = deal.get("config") or {}
    product_ids = {str(p) for p in (config.get("product_ids") or [])}
    if not product_ids:
        return []
    return [i for i in items if str(i.get("product_id") or "") in product_ids]
