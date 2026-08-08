from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
import httpx

from app.core import get_settings


@dataclass
class PlatformResult:
    ok: bool
    data: dict[str, Any]
    error: str | None = None


class PddPlatformAdapter(ABC):
    @abstractmethod
    async def get_order(self, user_id: str, order_id: str) -> PlatformResult: ...

    @abstractmethod
    async def get_logistics(self, user_id: str, order_id: str) -> PlatformResult: ...

    @abstractmethod
    async def get_stock(self, product_id: str) -> PlatformResult: ...

    @abstractmethod
    async def create_after_sales(self, user_id: str, order_id: str, action: str, reason: str) -> PlatformResult: ...


class MockPddPlatformAdapter(PddPlatformAdapter):
    orders = {
        "PDD20260806001": {
            "user_id": "buyer_001", "status": "已发货", "amount": 199.0,
            "item": "无线蓝牙耳机", "tracking_no": "SF1234567890", "carrier": "顺丰速运",
        },
        "PDD20260806002": {
            "user_id": "buyer_002", "status": "待发货", "amount": 89.0,
            "item": "保温杯", "tracking_no": None, "carrier": None,
        },
    }
    stock = {"PDD-G001": {"name": "无线蓝牙耳机", "price": 199.0, "stock": 48}}

    def _owned_order(self, user_id: str, order_id: str) -> PlatformResult:
        order = self.orders.get(order_id.upper())
        if not order:
            return PlatformResult(False, {}, "未找到该订单")
        if order["user_id"] != user_id:
            return PlatformResult(False, {}, "无权访问该订单")
        return PlatformResult(True, {"order_id": order_id.upper(), **order})

    async def get_order(self, user_id: str, order_id: str) -> PlatformResult:
        return self._owned_order(user_id, order_id)

    async def get_logistics(self, user_id: str, order_id: str) -> PlatformResult:
        result = self._owned_order(user_id, order_id)
        if not result.ok:
            return result
        if not result.data["tracking_no"]:
            return PlatformResult(True, {"order_id": order_id.upper(), "status": "待发货"})
        return PlatformResult(True, {
            "order_id": order_id.upper(), "status": "运输中", "carrier": result.data["carrier"],
            "tracking_no": result.data["tracking_no"], "latest_trace": "快件已到达配送站",
        })

    async def get_stock(self, product_id: str) -> PlatformResult:
        item = self.stock.get(product_id.upper())
        return PlatformResult(True, {"product_id": product_id.upper(), **item}) if item else PlatformResult(False, {}, "未找到商品")

    async def create_after_sales(self, user_id: str, order_id: str, action: str, reason: str) -> PlatformResult:
        result = self._owned_order(user_id, order_id)
        if not result.ok:
            return result
        return PlatformResult(True, {"after_sales_id": f"AS-{order_id[-4:]}", "status": "已受理", "action": action, "reason": reason})


class LivePddPlatformAdapter(PddPlatformAdapter):
    """Calls an enterprise gateway, not Pinduoduo directly; signing and platform scopes stay server-side."""

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.pdd_api_base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {settings.pdd_api_token}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> PlatformResult:
        if not self.base_url:
            return PlatformResult(False, {}, "PDD_API_BASE_URL 未配置")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.request(method, f"{self.base_url}{path}", headers=self.headers, **kwargs)
        if response.is_error:
            return PlatformResult(False, {}, f"平台 API 返回 {response.status_code}")
        return PlatformResult(True, response.json())

    async def get_order(self, user_id: str, order_id: str) -> PlatformResult:
        return await self._request("GET", f"/v1/orders/{order_id}", params={"buyer_id": user_id})

    async def get_logistics(self, user_id: str, order_id: str) -> PlatformResult:
        return await self._request("GET", f"/v1/orders/{order_id}/logistics", params={"buyer_id": user_id})

    async def get_stock(self, product_id: str) -> PlatformResult:
        return await self._request("GET", f"/v1/products/{product_id}/stock")

    async def create_after_sales(self, user_id: str, order_id: str, action: str, reason: str) -> PlatformResult:
        return await self._request("POST", "/v1/after-sales", json={"buyer_id": user_id, "order_id": order_id, "action": action, "reason": reason})


class DatabasePddPlatformAdapter(PddPlatformAdapter):
    async def _order(self, user_id: str, order_id: str) -> PlatformResult:
        settings = get_settings()
        if not settings.database_url:
            return PlatformResult(False, {}, "??????????")
        try:
            import asyncpg
            connection = await asyncpg.connect(settings.database_url)
            try:
                row = await connection.fetchrow("SELECT order_id, buyer_id, status, amount, item, carrier, tracking_no, latest_trace FROM demo_orders WHERE order_id=$1 AND buyer_id=$2", order_id.upper(), user_id)
            finally:
                await connection.close()
        except Exception:
            return PlatformResult(False, {}, "???????????")
        if not row:
            return PlatformResult(False, {}, "???????????")
        return PlatformResult(True, dict(row))

    async def get_order(self, user_id: str, order_id: str) -> PlatformResult:
        return await self._order(user_id, order_id)

    async def get_logistics(self, user_id: str, order_id: str) -> PlatformResult:
        result = await self._order(user_id, order_id)
        if not result.ok:
            return result
        if not result.data.get("tracking_no"):
            return PlatformResult(True, {"order_id": result.data["order_id"], "status": result.data["status"]})
        return PlatformResult(True, {key: result.data[key] for key in ("order_id", "status", "carrier", "tracking_no", "latest_trace")})

    async def get_stock(self, product_id: str) -> PlatformResult:
        return PlatformResult(False, {}, "???????????")

    async def create_after_sales(self, user_id: str, order_id: str, action: str, reason: str) -> PlatformResult:
        result = await self._order(user_id, order_id)
        if not result.ok:
            return result
        return PlatformResult(True, {"after_sales_id": f"AS-{order_id[-4:]}", "status": "???", "action": action, "reason": reason})


def get_pdd_adapter() -> PddPlatformAdapter:
    return LivePddPlatformAdapter() if get_settings().pdd_adapter_mode == "live" else DatabasePddPlatformAdapter() if get_settings().pdd_adapter_mode == "database" else MockPddPlatformAdapter()

