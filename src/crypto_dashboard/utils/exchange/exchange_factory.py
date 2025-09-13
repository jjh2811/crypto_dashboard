"""
Exchange Factory Module
This module is responsible for creating and configuring ccxt exchange instances.
It adapts exchange-specific methods to a standardized interface.
"""
import ccxt.pro as ccxt
from typing import Any, Dict, Optional

from ...protocols import ExchangeProtocol

async def _binance_create_oco_order(self: ExchangeProtocol, symbol: str, side: str, amount: float, price: Optional[float], stop_price: Optional[float], stop_limit_price: Optional[float] = None, params: Dict[str, Any] = {}) -> Dict[str, Any]:
    """
    Standardized OCO order creation method for Binance.
    This function will be attached to the ccxt.binance instance at runtime.
    """
    if price is None:
        raise ValueError("OCO 주문에는 price가 필수입니다.")

    if stop_price is None:
        raise ValueError("OCO 주문에는 stop_price가 필수입니다.")

    # Binance API requires the symbol without '/'
    api_params = {
        'symbol': self.market(symbol)['id'],
        'side': side.upper(),
        'quantity': float(self.amount_to_precision(symbol, amount)),
        'price': float(self.price_to_precision(symbol, price)),
        'stopPrice': float(self.price_to_precision(symbol, stop_price)),
    }

    # Add stopLimitPrice only if it's a stop-limit OCO order
    if stop_limit_price is not None:
        api_params['stopLimitPrice'] = float(self.price_to_precision(symbol, stop_limit_price))
        # Binance requires stopLimitTimeInForce for stop-limit OCO orders
        api_params['stopLimitTimeInForce'] = 'GTC'
    
    # Add any extra params passed in
    api_params.update(params)

    # The implicit method name for POST /api/v3/order/oco
    return await self.private_post_order_oco(api_params)

def get_exchange(exchange_name: str, api_key: str, api_secret: str) -> ExchangeProtocol:
    """
    Creates a ccxt exchange instance and attaches standardized methods.
    """
    exchange_class = getattr(ccxt, exchange_name)
    exchange: ExchangeProtocol = exchange_class({'apiKey': api_key, 'secret': api_secret})

    # Attach exchange-specific standardized methods
    if exchange_name == 'binance':
        # Attach the OCO method with a standard name
        setattr(exchange, 'create_oco_order', _binance_create_oco_order.__get__(exchange, exchange.__class__))

    # Add other exchange adaptations here in the future
    # elif exchange_name == 'bybit':
    #     setattr(exchange, 'create_oco_order', _bybit_create_oco_order.__get__(exchange, ccxt.Exchange))

    return exchange
