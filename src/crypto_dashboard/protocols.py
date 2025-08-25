from typing import Any, Dict, List, Protocol, TypedDict


class Balances(TypedDict):
    info: Dict[str, Any]
    free: Dict[str, float]
    used: Dict[str, float]
    total: Dict[str, float]


class Order(TypedDict):
    id: str
    symbol: str
    side: str
    price: float
    amount: float
    value: float
    timestamp: int
    status: str


class ExchangeProtocol(Protocol):
    apiKey: str
    secret: str

    async def fetch_balance(self, params: Dict[str, Any] = {}) -> Balances:
        ...

    async def fetch_open_orders(self, symbol: str | None = None, since: int | None = None, limit: int | None = None, params: Dict[str, Any] = {}) -> List[Order]:
        ...

    async def cancel_order(self, order_id: str, symbol: str | None = None, params: Dict[str, Any] = {}) -> Any:
        ...

    async def fetch_closed_orders(self, symbol: str | None = None, since: int | None = None, limit: int | None = None, params: Dict[str, Any] = {}) -> List[Order]:
        ...

    async def close(self) -> None:
        ...
