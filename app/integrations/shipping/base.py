from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class ShippingProviderError(Exception):
    """Raised when a shipping provider API call fails."""

    def __init__(self, message: str, *, status_code: int | None = None, response: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class ShippingProviderAdapter(ABC):
    """Adapter interface for third-party shipping order creation."""

    provider_id: str

    @abstractmethod
    async def create_order(self, order: Dict[str, Any], order_id: str) -> Dict[str, Any]:
        """
        Create an order in the external shipping platform.

        Returns a dict with at least:
          - external_order_id: str
          - order_number: str
          - status: str
          - raw_response: optional provider payload
        """
