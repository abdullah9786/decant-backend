import asyncio
import re
import unicodedata
from datetime import datetime, timezone
from typing import List, Optional, Union

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.schemas.product import ProductCreate, ProductUpdate
from app.services.chip_service import ChipService


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


class ProductService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.collection = db["products"]
        self.chip_service = ChipService(db)

    async def _unique_slug(self, base_slug: str, exclude_id: Optional[str] = None) -> str:
        slug = base_slug
        counter = 1
        while True:
            query: dict = {"slug": slug}
            if exclude_id:
                query["_id"] = {"$ne": ObjectId(exclude_id)}
            existing = await self.collection.find_one(query, {"_id": 1})
            if not existing:
                return slug
            slug = f"{base_slug}-{counter}"
            counter += 1

    async def get_all(
        self,
        fragrance_family: Optional[Union[str, List[str]]] = None,
        brand: Optional[Union[str, List[str]]] = None,
        is_featured: Optional[bool] = None,
        is_new_arrival: Optional[bool] = None,
        search: Optional[str] = None,
        sort_by: Optional[str] = None,
        include_inactive: bool = False,
        category_id: Optional[str] = None,
        product_type: Optional[str] = None,
        exclude_product_type: Optional[str] = None,
        featured_decant: Optional[bool] = None,
        featured_sealed_bottle: Optional[bool] = None,
        skip: Optional[int] = None,
        limit: Optional[int] = None,
        paginate: bool = False,
    ):
        query: dict = {}
        if not include_inactive:
            query["is_active"] = {"$ne": False}
        if fragrance_family:
            if isinstance(fragrance_family, list):
                query["fragrance_family"] = {"$in": fragrance_family}
            else:
                query["fragrance_family"] = fragrance_family
        if brand:
            if isinstance(brand, list):
                query["brand"] = {"$in": brand}
            else:
                query["brand"] = brand
        if category_id:
            query["category_ids"] = category_id
        if is_featured is not None:
            query["is_featured"] = is_featured
        if is_new_arrival is not None:
            query["is_new_arrival"] = is_new_arrival
        if product_type == "set":
            query["product_type"] = "set"
        elif product_type == "decant":
            query["product_type"] = {"$ne": "set"}
            query["variants"] = {"$elemMatch": {"is_pack": False}}
        elif product_type == "full-bottle":
            query["product_type"] = {"$ne": "set"}
            query["variants.is_pack"] = True
        elif product_type:
            query["product_type"] = product_type
        elif exclude_product_type:
            query["product_type"] = {"$ne": exclude_product_type}
        if featured_decant is not None:
            query["featured_decant"] = featured_decant
        if featured_sealed_bottle is not None:
            query["featured_sealed_bottle"] = featured_sealed_bottle
        if search:
            # Escape regex special chars so a query like "L'Eau (Issey)" doesn't
            # blow up the Mongo regex engine; substring + case-insensitive only.
            safe = re.escape(search.strip())
            query["$or"] = [
                {"name": {"$regex": safe, "$options": "i"}},
                {"brand": {"$regex": safe, "$options": "i"}},
                {"notes_top": {"$regex": safe, "$options": "i"}},
                {"notes_middle": {"$regex": safe, "$options": "i"}},
                {"notes_base": {"$regex": safe, "$options": "i"}},
                {"description": {"$regex": safe, "$options": "i"}},
            ]
        cursor = self.collection.find(query)
        if sort_by == "newest":
            cursor = cursor.sort("created_at", -1)
        elif sort_by == "price-asc":
            cursor = cursor.sort("variants.price", 1)
        elif sort_by == "price-desc":
            cursor = cursor.sort("variants.price", -1)
        else:
            cursor = cursor.sort([("sort_order", 1), ("created_at", -1)])

        skip_val = skip or 0
        limit_val = limit or 50
        total: Optional[int] = None
        if paginate:
            total = await self.collection.count_documents(query)
            cursor = cursor.skip(skip_val).limit(limit_val)

        products = await cursor.to_list(length=None)
        active_chips_by_id = await self._fetch_active_chips_by_id()
        # Read-only stock (no write-on-read), one bulk query for all set
        # components (instead of N+1), then in-memory chip attachment. This
        # replaces the previous serial per-product await loop.
        for product in products:
            self._ensure_stock_ml_readonly(product)
        await self._enrich_set_items_bulk(products)
        enriched = [self._attach_chips(product, active_chips_by_id) for product in products]

        if paginate and total is not None:
            return {
                "items": enriched,
                "total": total,
                "skip": skip_val,
                "limit": limit_val,
                "has_more": skip_val + len(enriched) < total,
            }
        return enriched

    async def search(
        self,
        q: str,
        limit: int = 12,
        skip: int = 0,
        include_inactive: bool = False,
    ):
        """Paginated text search over the product catalogue.

        Powers both the navbar autosuggest dropdown (small `limit`) and the
        full /search page (larger `limit` + Load More). Returns the matching
        items plus a `total` count so the frontend can decide whether to show
        the Load More button.

        Searchable fields: name, brand, notes_top/middle/base, description.
        Match is case-insensitive substring with regex special chars escaped
        — i.e. typing "(rose)" won't blow up the Mongo engine.

        For ranking, we boost name/brand matches above note/description
        matches by running two passes and concatenating the results. It's a
        pragmatic relevance hack — sufficient for our catalogue size and
        cheaper than adding a $text index + scoring pipeline right now.
        """
        q_clean = (q or "").strip()
        if not q_clean:
            return {"items": [], "total": 0, "has_more": False}

        safe = re.escape(q_clean)
        active_filter: dict = {}
        if not include_inactive:
            active_filter["is_active"] = {"$ne": False}

        # Pass 1: high-signal fields (product name, brand).
        primary_q = {
            **active_filter,
            "$or": [
                {"name": {"$regex": safe, "$options": "i"}},
                {"brand": {"$regex": safe, "$options": "i"}},
            ],
        }
        # Pass 2: lower-signal fields (notes, description). We'll exclude
        # anything already matched by pass 1 to avoid duplicates.
        secondary_q = {
            **active_filter,
            "$or": [
                {"notes_top": {"$regex": safe, "$options": "i"}},
                {"notes_middle": {"$regex": safe, "$options": "i"}},
                {"notes_base": {"$regex": safe, "$options": "i"}},
                {"description": {"$regex": safe, "$options": "i"}},
            ],
        }

        # Total count = union of both passes (Mongo $or naturally dedupes).
        union_q = {
            **active_filter,
            "$or": primary_q["$or"] + secondary_q["$or"],
        }
        total = await self.collection.count_documents(union_q)

        primary_ids: List[ObjectId] = []
        primary_docs: List[dict] = []
        async for doc in self.collection.find(primary_q).sort(
            [("sort_order", 1), ("created_at", -1)]
        ):
            primary_ids.append(doc["_id"])
            primary_docs.append(doc)

        secondary_docs: List[dict] = []
        if len(primary_docs) < skip + limit:
            secondary_query = {**secondary_q, "_id": {"$nin": primary_ids}}
            async for doc in self.collection.find(secondary_query).sort(
                [("sort_order", 1), ("created_at", -1)]
            ):
                secondary_docs.append(doc)

        combined = primary_docs + secondary_docs
        window = combined[skip : skip + limit]

        active_chips_by_id = await self._fetch_active_chips_by_id()
        normalized = []
        for product in window:
            product = await self._ensure_stock_ml(product)
            product = await self._enrich_set_items(product)
            normalized.append(self._attach_chips(product, active_chips_by_id))

        return {
            "items": normalized,
            "total": total,
            "has_more": skip + len(window) < total,
        }

    async def get_by_id(self, product_id: str):
        product = await self.collection.find_one({"_id": ObjectId(product_id)})
        product = await self._ensure_stock_ml(product)
        product = await self._enrich_set_items(product)
        return await self._attach_chips_single(product)

    async def get_by_slug(self, slug: str):
        product = await self.collection.find_one({"slug": slug})
        product = await self._ensure_stock_ml(product)
        product = await self._enrich_set_items(product)
        return await self._attach_chips_single(product)

    async def get_by_id_or_slug(self, identifier: str):
        if ObjectId.is_valid(identifier):
            product = await self.get_by_id(identifier)
            if product:
                return product
        return await self.get_by_slug(identifier)

    async def _build_related_context(self, source: dict) -> dict:
        """Collect families, brands, categories, and chips used to score related items."""
        families: set[str] = set()
        brands: set[str] = set()
        category_ids = {str(c) for c in (source.get("category_ids") or [])}
        chip_ids = {str(c) for c in (source.get("chip_ids") or [])}

        family = (source.get("fragrance_family") or "").strip()
        if family:
            families.add(family)
        brand = (source.get("brand") or "").strip()
        if brand:
            brands.add(brand)

        if source.get("product_type") == "set":
            set_pids: list[ObjectId] = []
            for item in source.get("set_items") or []:
                pid = str(item.get("product_id") or "")
                if ObjectId.is_valid(pid):
                    set_pids.append(ObjectId(pid))
            if set_pids:
                cursor = self.collection.find(
                    {"_id": {"$in": set_pids}},
                    {"fragrance_family": 1, "brand": 1, "category_ids": 1},
                )
                comps = await cursor.to_list(length=len(set_pids))
                for comp in comps:
                    comp_family = (comp.get("fragrance_family") or "").strip()
                    if comp_family:
                        families.add(comp_family)
                    comp_brand = (comp.get("brand") or "").strip()
                    if comp_brand:
                        brands.add(comp_brand)
                    for cid in comp.get("category_ids") or []:
                        category_ids.add(str(cid))

        return {
            "families": families,
            "brands": brands,
            "category_ids": category_ids,
            "chip_ids": chip_ids,
        }

    def _product_note_set(self, product: dict) -> set[str]:
        notes: list[str] = []
        notes.extend(product.get("notes_top") or [])
        notes.extend(product.get("notes_middle") or [])
        notes.extend(product.get("notes_base") or [])
        return {n.strip().lower() for n in notes if isinstance(n, str) and n.strip()}

    def _score_related_product(self, source: dict, candidate: dict, ctx: dict) -> float:
        if str(source.get("_id")) == str(candidate.get("_id")):
            return -1.0

        score = 0.0
        family = (candidate.get("fragrance_family") or "").strip()
        if family and family in ctx["families"]:
            score += 4.0

        cand_cats = {str(c) for c in (candidate.get("category_ids") or [])}
        if cand_cats & ctx["category_ids"]:
            score += 3.0

        cand_chips = {str(c) for c in (candidate.get("chip_ids") or [])}
        if cand_chips & ctx["chip_ids"]:
            score += 2.0

        brand = (candidate.get("brand") or "").strip()
        if brand and brand in ctx["brands"]:
            score += 1.0

        note_overlap = self._product_note_set(source) & self._product_note_set(candidate)
        if note_overlap:
            score += min(len(note_overlap), 2)

        if source.get("product_type") == "set" and candidate.get("product_type") == "set":
            score += 1.0

        if candidate.get("is_featured"):
            score += 0.5

        return score

    def _is_in_stock(self, product: dict) -> bool:
        if product.get("product_type") == "set":
            set_items = product.get("set_items") or []
            decant_variants = [
                v for v in (product.get("variants") or [])
                if not v.get("is_pack") and int(v.get("size_ml") or 0) > 0
            ]
            if not set_items or not decant_variants:
                return False
            for variant in decant_variants:
                size_ml = int(variant.get("size_ml") or 0)
                if all(int(item.get("stock_ml") or 0) >= size_ml for item in set_items):
                    return True
            return False

        variants = product.get("variants") or []
        if not variants:
            return False
        stock_ml = int(product.get("stock_ml") or 0)
        for variant in variants:
            if variant.get("is_pack"):
                if int(variant.get("stock") or 0) >= 1:
                    return True
            elif stock_ml >= int(variant.get("size_ml") or 0):
                return True
        return False

    async def _normalize_product_for_list(self, product: dict, active_chips_by_id: dict) -> dict:
        product = await self._ensure_stock_ml(product)
        product = await self._enrich_set_items(product)
        return self._attach_chips(product, active_chips_by_id)

    async def _normalize_many_for_list(
        self,
        raws: List[dict],
        active_chips_by_id: dict,
        *,
        concurrency: int = 32,
    ) -> List[dict]:
        """Normalize many product docs in parallel (bounded concurrency).

        Replaces sequential awaits in get_related — major PDP latency win.
        """
        if not raws:
            return []
        sem = asyncio.Semaphore(concurrency)

        async def _one(raw: dict) -> dict:
            async with sem:
                return await self._normalize_product_for_list(raw, active_chips_by_id)

        return list(await asyncio.gather(*(_one(r) for r in raws)))

    async def get_related(self, identifier: str, limit: int = 10) -> Optional[List[dict]]:
        source = await self.get_by_id_or_slug(identifier)
        if not source:
            return None

        ctx = await self._build_related_context(source)
        or_clauses: list[dict] = []
        if ctx["families"]:
            or_clauses.append({"fragrance_family": {"$in": list(ctx["families"])}})
        if ctx["brands"]:
            or_clauses.append({"brand": {"$in": list(ctx["brands"])}})
        if ctx["category_ids"]:
            or_clauses.append({"category_ids": {"$in": list(ctx["category_ids"])}})
        if ctx["chip_ids"]:
            or_clauses.append({"chip_ids": {"$in": list(ctx["chip_ids"])}})

        query: dict = {
            "is_active": {"$ne": False},
            "_id": {"$ne": source["_id"]},
        }
        if or_clauses:
            query["$or"] = or_clauses

        cursor = self.collection.find(query).sort([("sort_order", 1), ("created_at", -1)])
        raw_candidates = await cursor.to_list(length=100)
        active_chips_by_id = await self._fetch_active_chips_by_id()

        normalized = await self._normalize_many_for_list(raw_candidates, active_chips_by_id)
        by_id = {str(p["_id"]): p for p in normalized}

        scored: list[tuple[float, int, int, dict]] = []
        seen_ids: set[str] = set()
        for product in normalized:
            pid = str(product["_id"])
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            relevance = self._score_related_product(source, product, ctx)
            if relevance <= 0:
                continue
            scored.append((
                relevance,
                1 if self._is_in_stock(product) else 0,
                int(product.get("sort_order") or 0),
                product,
            ))

        scored.sort(key=lambda row: (-row[0], -row[1], row[2]))

        if len(scored) < limit:
            scored_ids = {str(row[3]["_id"]) for row in scored}
            for raw in raw_candidates:
                if len(scored) >= limit:
                    break
                pid = str(raw["_id"])
                if pid in scored_ids:
                    continue
                product = by_id[pid]
                scored.append((
                    0.0,
                    1 if self._is_in_stock(product) else 0,
                    int(product.get("sort_order") or 0),
                    product,
                ))
                scored_ids.add(pid)
            scored.sort(key=lambda row: (-row[0], -row[1], row[2]))

        if len(scored) < limit:
            fallback_cursor = self.collection.find({
                "is_active": {"$ne": False},
                "_id": {"$ne": source["_id"]},
            }).sort([("sort_order", 1), ("created_at", -1)])
            fallback_raw = await fallback_cursor.to_list(length=50)
            picked = {str(row[3]["_id"]) for row in scored}
            if fallback_raw:
                normed_fb = await self._normalize_many_for_list(fallback_raw, active_chips_by_id)
                for product in normed_fb:
                    if len(scored) >= limit:
                        break
                    pid = str(product["_id"])
                    if pid in picked:
                        continue
                    scored.append((
                        0.0,
                        1 if self._is_in_stock(product) else 0,
                        int(product.get("sort_order") or 0),
                        product,
                    ))
                    picked.add(pid)
            scored.sort(key=lambda row: (-row[0], -row[1], row[2]))

        return [row[3] for row in scored[:limit]]

    async def create(self, product_in: ProductCreate):
        product_dict = product_in.dict()
        await self._validate_product(product_dict)
        product_dict["created_at"] = product_dict.get("created_at") or datetime.now(timezone.utc)
        if product_dict.get("product_type") == "set":
            product_dict["stock_ml"] = 0
            if not (product_dict.get("brand") or "").strip():
                product_dict["brand"] = "Curated"
            base_slug = _slugify(product_dict["name"])
        else:
            base_slug = _slugify(f"{product_dict['name']} {product_dict['brand']}")
        product_dict["slug"] = await self._unique_slug(base_slug)
        product_result = await self.collection.insert_one(product_dict)
        return await self.get_by_id(str(product_result.inserted_id))

    async def update(self, product_id: str, product_in: ProductUpdate):
        update_data = {k: v for k, v in product_in.dict(exclude_unset=True).items()}
        current = await self.collection.find_one({"_id": ObjectId(product_id)})
        if not current:
            return None
        merged = {**current, **update_data}
        await self._validate_product(merged, exclude_id=product_id)
        if merged.get("product_type") == "set":
            update_data["stock_ml"] = 0
            if not (merged.get("brand") or "").strip():
                update_data["brand"] = "Curated"
        if "name" in update_data or "brand" in update_data or "product_type" in update_data:
            name = update_data.get("name", current.get("name", ""))
            brand = update_data.get("brand", current.get("brand", ""))
            is_set = merged.get("product_type") == "set"
            base_slug = _slugify(name) if is_set else _slugify(f"{name} {brand}")
            update_data["slug"] = await self._unique_slug(base_slug, exclude_id=product_id)
        await self.collection.update_one(
            {"_id": ObjectId(product_id)}, {"$set": update_data}
        )
        return await self.get_by_id(product_id)

    async def delete(self, product_id: str):
        return await self.collection.delete_one({"_id": ObjectId(product_id)})

    async def _ensure_stock_ml(self, product: Optional[dict]):
        if not product:
            return product
        if product.get("product_type") == "set":
            return product
        if product.get("stock_ml") is None:
            variants = product.get("variants", [])
            computed = 0
            for v in variants:
                try:
                    computed += int(v.get("size_ml", 0)) * int(v.get("stock", 0))
                except Exception:
                    continue
            product["stock_ml"] = computed
            await self.collection.update_one(
                {"_id": product["_id"]},
                {"$set": {"stock_ml": computed}},
            )
        return product

    def _ensure_stock_ml_readonly(self, product: Optional[dict]) -> Optional[dict]:
        """In-memory variant of _ensure_stock_ml — never writes back to Mongo.

        Used on read-heavy list paths (category grid, product listings) so a GET
        can't trigger a write per product. The canonical single-product reads
        still backfill the persisted value.
        """
        if not product:
            return product
        if product.get("product_type") == "set":
            return product
        if product.get("stock_ml") is None:
            computed = 0
            for v in product.get("variants", []) or []:
                try:
                    computed += int(v.get("size_ml", 0)) * int(v.get("stock", 0))
                except Exception:
                    continue
            product["stock_ml"] = computed
        return product

    async def _enrich_set_items_bulk(self, products: List[dict]) -> None:
        """Resolve set component display fields for many products in ONE query.

        Equivalent to calling _enrich_set_items per product, but collapses the
        N+1 component lookups into a single `$in` query for the whole page.
        Mutates each set product's `set_items` in place.
        """
        set_products = [p for p in products if p.get("product_type") == "set"]
        if not set_products:
            return

        all_oids: list[ObjectId] = []
        seen: set[str] = set()
        for product in set_products:
            for item in product.get("set_items") or []:
                pid = str(item.get("product_id") or "")
                if ObjectId.is_valid(pid) and pid not in seen:
                    seen.add(pid)
                    all_oids.append(ObjectId(pid))

        comps_by_id: dict[str, dict] = {}
        if all_oids:
            cursor = self.collection.find(
                {"_id": {"$in": all_oids}},
                {"name": 1, "brand": 1, "image_url": 1, "slug": 1, "stock_ml": 1},
            )
            found = await cursor.to_list(length=len(all_oids))
            comps_by_id = {str(doc["_id"]): doc for doc in found}

        for product in set_products:
            enriched = []
            for item in product.get("set_items") or []:
                pid = str(item.get("product_id") or "")
                comp = comps_by_id.get(pid) if ObjectId.is_valid(pid) else None
                enriched.append({
                    "product_id": pid,
                    "name": comp.get("name") if comp else item.get("name", ""),
                    "brand": comp.get("brand") if comp else item.get("brand", ""),
                    "image_url": comp.get("image_url") if comp else item.get("image_url"),
                    "slug": comp.get("slug") if comp else item.get("slug"),
                    "stock_ml": int(comp.get("stock_ml") or 0) if comp else 0,
                })
            product["set_items"] = enriched

    async def _fetch_active_chips_by_id(self) -> dict:
        """Build a single dict of active chips keyed by their string id.

        Used by get_all() to avoid one chip lookup per product.
        """
        active = await self.chip_service.get_active()
        return {str(chip["_id"]): chip for chip in active}

    def _attach_chips(self, product: Optional[dict], active_chips_by_id: dict) -> Optional[dict]:
        if not product:
            return product
        chip_ids = product.get("chip_ids") or []
        resolved = []
        for cid in chip_ids:
            chip = active_chips_by_id.get(str(cid))
            if not chip:
                continue
            resolved.append({
                "_id": str(chip["_id"]),
                "code": chip.get("code"),
                "label": chip.get("label"),
                "color": chip.get("color", "indigo"),
                "icon": chip.get("icon"),
                "priority": chip.get("priority", 0),
            })
        resolved.sort(key=lambda c: (c.get("priority", 0), c.get("label", "")))
        product["chips"] = resolved
        return product

    async def _attach_chips_single(self, product: Optional[dict]) -> Optional[dict]:
        if not product:
            return product
        chip_ids = product.get("chip_ids") or []
        if not chip_ids:
            product["chips"] = []
            return product
        active = await self.chip_service.get_active_by_ids(chip_ids)
        active_by_id = {str(c["_id"]): c for c in active}
        return self._attach_chips(product, active_by_id)

    async def bulk_update_chips(self, product_ids: List[str], add: List[str], remove: List[str]):
        if not product_ids:
            return {"matched": 0, "modified": 0}
        object_ids = []
        for pid in product_ids:
            try:
                object_ids.append(ObjectId(pid))
            except Exception:
                continue

        modified = 0
        matched = 0
        if remove:
            res = await self.collection.update_many(
                {"_id": {"$in": object_ids}},
                {"$pullAll": {"chip_ids": remove}},
            )
            matched = max(matched, res.matched_count)
            modified += res.modified_count
        if add:
            res = await self.collection.update_many(
                {"_id": {"$in": object_ids}},
                {"$addToSet": {"chip_ids": {"$each": add}}},
            )
            matched = max(matched, res.matched_count)
            modified += res.modified_count
        return {"matched": matched, "modified": modified}

    async def _validate_product(self, product_dict: dict, exclude_id: Optional[str] = None):
        product_type = product_dict.get("product_type") or "single"
        if product_type != "set":
            return

        set_items = product_dict.get("set_items") or []
        if len(set_items) < 2:
            raise ValueError("A set must include at least 2 fragrances.")

        normalized_items = []
        for item in set_items:
            pid = str(item.get("product_id") or "")
            if not ObjectId.is_valid(pid):
                raise ValueError(f"Invalid product in set: {pid or 'unknown'}")
            if exclude_id and pid == exclude_id:
                raise ValueError("A set cannot include itself.")
            comp = await self.collection.find_one({"_id": ObjectId(pid)})
            if not comp:
                raise ValueError("One or more set fragrances were not found.")
            if comp.get("product_type") == "set":
                raise ValueError("Set items must be single fragrances, not other sets.")
            normalized_items.append({"product_id": pid})

        product_dict["set_items"] = normalized_items

        variants = product_dict.get("variants") or []
        decant_variants = [
            v for v in variants
            if not v.get("is_pack") and int(v.get("size_ml") or 0) > 0 and float(v.get("price") or 0) > 0
        ]
        if len(decant_variants) == 0:
            raise ValueError("Please add at least one set size with a price.")

        for variant in decant_variants:
            size_ml = int(variant.get("size_ml") or 0)
            for item in normalized_items:
                comp = await self.collection.find_one({"_id": ObjectId(item["product_id"])})
                has_decant_variant = any(
                    int(v.get("size_ml", 0)) == size_ml and not v.get("is_pack")
                    for v in (comp or {}).get("variants", [])
                )
                if not has_decant_variant:
                    raise ValueError(
                        f"{comp.get('name', 'Product')} does not offer a {size_ml}ml decant required for this set size."
                    )

    async def _enrich_set_items(self, product: Optional[dict]):
        if not product or product.get("product_type") != "set":
            return product

        items = product.get("set_items") or []
        oids: list[ObjectId] = []
        pid_order: list[str] = []
        for item in items:
            pid = str(item.get("product_id") or "")
            if ObjectId.is_valid(pid):
                oid = ObjectId(pid)
                oids.append(oid)
                pid_order.append(pid)

        comps_by_id: dict[str, dict] = {}
        if oids:
            cursor = self.collection.find({"_id": {"$in": oids}})
            found = await cursor.to_list(length=len(oids))
            comps_by_id = {str(doc["_id"]): doc for doc in found}

        enriched = []
        for item in items:
            pid = str(item.get("product_id") or "")
            comp = comps_by_id.get(pid) if ObjectId.is_valid(pid) else None
            enriched.append({
                "product_id": pid,
                "name": comp.get("name") if comp else item.get("name", ""),
                "brand": comp.get("brand") if comp else item.get("brand", ""),
                "image_url": comp.get("image_url") if comp else item.get("image_url"),
                "slug": comp.get("slug") if comp else item.get("slug"),
                "stock_ml": int(comp.get("stock_ml", 0)) if comp else 0,
            })
        product["set_items"] = enriched
        return product
