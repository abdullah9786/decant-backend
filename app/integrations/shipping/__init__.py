from __future__ import annotations

from typing import Dict

from app.integrations.shipping.base import ShippingProviderAdapter, ShippingProviderError
from app.integrations.shipping.nimbuspost import NimbusPostAdapter

_PROVIDERS: Dict[str, ShippingProviderAdapter] = {
    "nimbuspost": NimbusPostAdapter(),
}


def get_shipping_provider(provider: str) -> ShippingProviderAdapter:
    adapter = _PROVIDERS.get((provider or "").lower())
    if not adapter:
        raise KeyError(provider)
    return adapter


def supported_providers() -> list[str]:
    return list(_PROVIDERS.keys())


__all__ = [
    "ShippingProviderAdapter",
    "ShippingProviderError",
    "NimbusPostAdapter",
    "get_shipping_provider",
    "supported_providers",
]
