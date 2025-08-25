import asyncio
from decimal import Decimal
import json
import logging
import os
from typing import Any, Dict, Optional, cast
from abc import ABC, abstractmethod

from aiohttp import web
from websockets.exceptions import ConnectionClosed, ConnectionClosedError
from websockets.legacy.client import WebSocketClientProtocol, connect
from websockets.protocol import State

from .exchange_utils import calculate_average_buy_price
from .protocols import ExchangeProtocol, Balances


class ExchangeBase(ABC):
    def __init__(self, api_key: str, secret_key: str, app: web.Application, exchange_name: str) -> None:
        self.name = exchange_name
        self.logger = logging.getLogger(exchange_name)
        self.app = app

        with open(os.path.join(os.path.dirname(__file__), 'config.json')) as f:
            config = json.load(f)
        
        exchange_config = config['exchanges'][self.name.lower()]
        self.quote_currency = exchange_config.get('quote_currency')
        
        self._exchange = self._create_exchange_instance(api_key, secret_key, exchange_config)

        self.balances_cache: Dict[str, Dict[str, Any]] = {}
        self.orders_cache: Dict[str, Dict[str, Any]] = {}
        self.userdata_ws: Optional[WebSocketClientProtocol] = None
        self.price_ws: Optional[WebSocketClientProtocol] = None
        self.price_ws_connected_event = asyncio.Event()
        self.user_data_subscribed_event = asyncio.Event()
        self.tracked_assets = set()
        self._ws_id_counter = 1

    @abstractmethod
    def _create_exchange_instance(self, api_key: str, secret_key: str, exchange_config: Dict[str, Any]) -> ExchangeProtocol:
        raise NotImplementedError

    @property
    def exchange(self) -> ExchangeProtocol:
        return cast(ExchangeProtocol, self._exchange)

    def create_balance_update_message(self, symbol, balance_data):
        """잔고 정보로부터 클라이언트에게 보낼 업데이트 메시지를 생성합니다."""
        price = Decimal(str(balance_data.get('price', '0')))
        free_amount = balance_data.get('free', Decimal('0'))
        locked_amount = balance_data.get('locked', Decimal('0'))
        total_amount = free_amount + locked_amount
        value = price * total_amount
        avg_buy_price = balance_data.get('avg_buy_price')
        realised_pnl = balance_data.get('realised_pnl')

        unrealised_pnl = None
        if avg_buy_price is not None and price > 0:
            unrealised_pnl = (price - avg_buy_price) * total_amount

        message = {
            'type': 'balance_update',
            'exchange': self.name,
            'symbol': symbol,
            'price': float(price),
            'free': float(free_amount),
            'locked': float(locked_amount),
            'value': float(value),
            'avg_buy_price': float(avg_buy_price) if avg_buy_price is not None else None,
            'realised_pnl': float(realised_pnl) if realised_pnl is not None else None,
            'unrealised_pnl': float(unrealised_pnl) if unrealised_pnl is not None else None,
            'quote_currency': self.quote_currency
        }

        reference_prices = self.app.get('reference_prices', {})
        reference_time = self.app.get('reference_time')

        if reference_prices and self.name in reference_prices and symbol in reference_prices[self.name]:
            ref_price = Decimal(str(reference_prices[self.name][symbol]))
            if ref_price > 0:
                price_change_percent = (price - ref_price) / ref_price * 100
                message['price_change_percent'] = float(price_change_percent)
                message['reference_time'] = reference_time

        return message

    async def get_initial_data(self) -> None:
        try:
            balance, open_orders = await asyncio.gather(
                self.exchange.fetch_balance(),
                self.exchange.fetch_open_orders()
            )

            for order in open_orders:
                price = Decimal(str(order.get('price') or 0))
                amount = Decimal(str(order.get('amount') or 0))
                order_id = order.get('id')
                if order_id:
                    self.orders_cache[order_id] = {
                        'id': order_id, 'symbol': order.get('symbol'), 'side': order.get('side'),
                        'price': float(price), 'amount': float(amount), 'value': float(price * amount),
                        'quote_currency': order.get('symbol', '').split('/')[1] if order.get('symbol') and '/' in order.get('symbol', '') else self.quote_currency,
                        'timestamp': order.get('timestamp'), 'status': order.get('status')
                    }
            self.logger.info(f"Fetched {len(open_orders)} open orders at startup.")

            total_balances = {
                asset: total for asset, total in balance.get('total', {}).items() if total > 0
            }
            
            await self._process_initial_balances(balance, total_balances)

        except Exception as e:
            self.logger.error(f"Failed to fetch initial data from {self.name}: {e}")
            await self.exchange.close()
            raise

        holding_assets = set(self.balances_cache.keys())
        order_assets = {o['symbol'].replace(self.quote_currency, '').replace('/', '') for o in self.orders_cache.values()}
        self.tracked_assets = holding_assets | order_assets

    async def _process_initial_balances(self, balance: Balances, total_balances: Dict[str, float]):
        for asset, total_amount in total_balances.items():
            avg_buy_price, realised_pnl = await calculate_average_buy_price(self.exchange, asset, Decimal(str(total_amount)), self.quote_currency, self.logger)
            free_amount = Decimal(str(balance.get('free', {}).get(asset, 0)))
            locked_amount = Decimal(str(balance.get('used', {}).get(asset, 0)))
            self.balances_cache[asset] = {
                'free': free_amount,
                'locked': locked_amount,
                'total_amount': free_amount + locked_amount,
                'price': Decimal('1.0') if asset == self.quote_currency else Decimal('0'),
                'avg_buy_price': avg_buy_price,
                'realised_pnl': realised_pnl
            }
            self.logger.info(f"Asset: {asset}, Avg Buy Price: {avg_buy_price if avg_buy_price is not None else 'N/A'}, Realised PnL: {realised_pnl}")

    @abstractmethod
    async def connect_price_ws(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def connect_user_data_ws(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def update_subscriptions_if_needed(self) -> None:
        raise NotImplementedError

    async def cancel_order(self, order_id: str, symbol: str) -> None:
        try:
            await self.exchange.cancel_order(order_id, symbol)
            self.logger.info(f"Successfully sent cancel request for order {order_id}")
            await self.app['broadcast_log']({'status': 'Cancelling', 'symbol': symbol, 'order_id': order_id}, self.name)
        except Exception as e:
            self.logger.error(f"Failed to cancel order {order_id}: {e}")
            await self.app['broadcast_log']({'status': 'Cancel Failed', 'symbol': symbol, 'order_id': order_id, 'reason': str(e)}, self.name)

    async def cancel_all_orders(self) -> None:
        self.logger.info("Received request to cancel all orders.")
        all_orders = list(self.orders_cache.values())
        if not all_orders:
            self.logger.info("No open orders to cancel.")
            await self.app['broadcast_log']({'status': 'Info', 'message': 'No open orders to cancel.'}, self.name)
            return

        await self.app['broadcast_log']({'status': 'Info', 'message': f'Cancelling all {len(all_orders)} orders.'}, self.name)

        for order in all_orders:
            order_id = order.get('id')
            symbol = order.get('symbol')
            if not order_id or not symbol:
                continue
            try:
                await self.exchange.cancel_order(order_id, symbol)
                self.logger.info(f"Successfully sent cancel request for order {order_id}")
            except Exception as e:
                self.logger.error(f"Failed to cancel order {order_id}: {e}")
                await self.app['broadcast_log']({'status': 'Cancel Failed', 'symbol': symbol, 'order_id': order_id, 'reason': str(e)}, self.name)

        self.orders_cache.clear()

    async def close(self) -> None:
        await self.exchange.close()
