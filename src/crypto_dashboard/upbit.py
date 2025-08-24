import asyncio
from decimal import Decimal
import json
import logging
import time
import os
import uuid
import jwt
from typing import Any, Dict, List, Optional, cast

from aiohttp import web
from ccxt.async_support import upbit
from websockets.exceptions import ConnectionClosed, ConnectionClosedError
from websockets.legacy.client import WebSocketClientProtocol, connect
from websockets.protocol import State

from .exchange_utils import calculate_average_buy_price
from .protocols import ExchangeProtocol


class UpbitExchange:
    def __init__(self, api_key: str, secret_key: str, app: web.Application, exchange_name: str) -> None:
        self.name = exchange_name
        self.logger = logging.getLogger(exchange_name)
        with open(os.path.join(os.path.dirname(__file__), 'config.json')) as f:
            config = json.load(f)
        
        upbit_config = config['exchanges']['upbit']
        self.quote_currency = upbit_config.get('quote_currency', 'KRW')

        self.price_ws_url = upbit_config['price_ws_url']
        self.user_data_ws_url = upbit_config['user_data_ws_url']

        self._exchange = upbit({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
                'warnOnFetchOpenOrdersWithoutSymbol': False,
            },
        })

        self.app = app
        self.balances_cache: Dict[str, Dict[str, Any]] = {}
        self.orders_cache: Dict[str, Dict[str, Any]] = {}
        self.wscp: Optional[WebSocketClientProtocol] = None
        self.price_ws: Optional[WebSocketClientProtocol] = None
        self.price_ws_ready = asyncio.Event()
        self.tracked_assets = set()
        self._ws_id_counter = 1

    @property
    def exchange(self) -> ExchangeProtocol:
        return cast(ExchangeProtocol, self._exchange)

    def _get_auth_headers(self) -> dict:
        """웹소켓 연결을 위한 인증 헤더를 생성합니다."""
        payload = {
            'access_key': self.exchange.apiKey,
            'nonce': str(uuid.uuid4())
        }
        jwt_token = jwt.encode(payload, self.exchange.secret)
        authorization_token = f'Bearer {jwt_token}'
        return {"Authorization": authorization_token}

    async def _send_price_subscription(self, websocket: WebSocketClientProtocol) -> None:
        """Constructs and sends a price subscription request to the websocket."""
        assets_to_subscribe = [f"{self.quote_currency}-{asset}" for asset in self.tracked_assets if asset != self.quote_currency]
        
        # 구독할 자산이 없으면, 기존 구독을 취소하기 위해 빈 문자열을 포함한 리스트를 보냅니다.
        codes_to_send = assets_to_subscribe if assets_to_subscribe else ['']

        request = [
            {"ticket": str(uuid.uuid4())},
            {"type": "ticker", "codes": codes_to_send, "isOnlyRealtime": True},
            {"format": "SIMPLE"}
        ]

        await websocket.send(json.dumps(request))
        self.logger.info(f"Subscription request sent for: {codes_to_send}")

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
            balance = await self.exchange.fetch_balance()
            open_orders = await self.exchange.fetch_open_orders()

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

        except Exception as e:
            self.logger.error(f"Failed to fetch initial data from Upbit: {e}")
            await self.exchange.close()
            raise

        holding_assets = set(self.balances_cache.keys())
        order_assets = {o['symbol'].replace(self.quote_currency, '').replace('/', '') for o in self.orders_cache.values()}
        self.tracked_assets = holding_assets | order_assets

    async def connect_price_ws(self) -> None:
        while True:
            try:
                async with connect(self.price_ws_url) as websocket:
                    self.price_ws = websocket
                    self.logger.info("Upbit price data websocket connection established.")
                    self.price_ws_ready.set()

                    await self._send_price_subscription(websocket)

                    async for message in websocket:
                        try:
                            if not message:
                                continue
                            message_text = message.decode('utf-8') if isinstance(message, bytes) else message
                            data = json.loads(message_text)
                            self.logger.info(f"Received data from Upbit price ws: {data}")

                            # 데이터가 딕셔너리인 경우에만 기존 로직을 실행하도록 수정
                            if isinstance(data, dict) and data.get('ty') == 'ticker':
                                symbol = data.get('cd', '').replace(f"{self.quote_currency}-", '')
                                price = data.get('tp')

                                if not symbol or not price:
                                    continue
                                
                                self.logger.debug(f"Price received: {symbol} = {price}")

                                if symbol in self.balances_cache:
                                    self.balances_cache[symbol]['price'] = Decimal(str(price))
                                    update_message = self.create_balance_update_message(symbol, self.balances_cache[symbol])
                                else:
                                    update_message = {
                                        'type': 'price_update',
                                        'exchange': self.name,
                                        'symbol': symbol,
                                        'price': float(price)
                                    }
                                
                                await self.app['broadcast_message'](update_message)

                        except (json.JSONDecodeError, UnicodeDecodeError):
                            self.logger.error(f"Failed to decode message from Upbit price ws: {message}")


            except (ConnectionClosed, ConnectionClosedError):
                self.logger.warning("Upbit price data websocket connection closed. Reconnecting in 5 seconds...")
                self.price_ws_ready.clear()
                self.price_ws = None
                await asyncio.sleep(5)
            except Exception as e:
                self.logger.error(f"An error occurred in connect_price_ws for Upbit: {e}", exc_info=True)
                self.price_ws_ready.clear()
                self.price_ws = None
                await asyncio.sleep(5)

    async def connect_user_data_ws(self) -> None:
        self.logger.info(f"Connecting to Upbit User Data Stream: {self.user_data_ws_url}")
        while True:
            try:
                headers = self._get_auth_headers()
                async with connect(self.user_data_ws_url, extra_headers=headers) as websocket:
                    self.wscp = websocket
                    self.logger.info("Upbit User Data Stream connection established.")

                    # 구독 요청 메시지 전송
                    subscribe_request = [
                        {"ticket": str(uuid.uuid4())},
                        {"type": "myOrder"},
                        {"type": "myAsset"},
                        {"format": "SIMPLE"}
                    ]
                    await websocket.send(json.dumps(subscribe_request))
                    self.logger.info("Upbit user data subscription request sent.")

                    async for message in websocket:
                        try:
                            message_text = message.decode('utf-8') if isinstance(message, bytes) else message
                            data = json.loads(message_text)
                            self.logger.debug(f"Received from Upbit user ws: {data}")

                            event_type = data.get('ty')

                            if event_type == 'myAsset':
                                for balance_update in data.get('ast', []):
                                    asset = balance_update.get('cu')
                                    if not asset:
                                        continue

                                    free_amount = Decimal(str(balance_update.get('b', '0')))
                                    locked_amount = Decimal(str(balance_update.get('l', '0')))
                                    total_amount = free_amount + locked_amount

                                    is_existing = asset in self.balances_cache
                                    is_positive_total = total_amount > Decimal('0')

                                    if is_positive_total:
                                        if not is_existing:
                                            self.logger.info(f"New asset detected: {asset}, free: {free_amount}, locked: {locked_amount}")
                                            self.balances_cache[asset] = {'price': Decimal('0')}
                                        
                                        self.balances_cache[asset]['free'] = free_amount
                                        self.balances_cache[asset]['locked'] = locked_amount
                                        self.balances_cache[asset]['total_amount'] = total_amount
                                        
                                        if 'avg_buy_price' not in self.balances_cache[asset]:
                                            avg_buy_price, realised_pnl = await calculate_average_buy_price(self.exchange, asset, total_amount, self.quote_currency, self.logger)
                                            self.balances_cache[asset]['avg_buy_price'] = avg_buy_price
                                            self.balances_cache[asset]['realised_pnl'] = realised_pnl

                                        update_message = self.create_balance_update_message(asset, self.balances_cache[asset])
                                        await self.app['broadcast_message'](update_message)
                                        
                                        if not is_existing:
                                            await self.update_subscriptions_if_needed()

                                    elif not is_positive_total and is_existing:
                                        self.logger.info(f"Asset sold out or zeroed: {asset}")
                                        del self.balances_cache[asset]
                                        await self.app['broadcast_message']({'type': 'remove_holding', 'symbol': asset})
                                        await self.update_subscriptions_if_needed()

                            elif event_type == 'myOrder':
                                order_id = data.get('uid')
                                symbol_raw = data.get('cd', '')
                                status = data.get('s')
                                if not all([order_id, symbol_raw, status]):
                                    continue

                                symbol_parts = symbol_raw.split('-')
                                symbol = f"{symbol_parts[1]}/{symbol_parts[0]}" if len(symbol_parts) == 2 else symbol_raw
                                asset = symbol_parts[1] if len(symbol_parts) == 2 else symbol_raw.replace(self.quote_currency, '')

                                side_map = {'ASK': 'sell', 'BID': 'buy'}
                                side = side_map.get(data.get('ab'))
                                price = Decimal(str(data.get('p', '0')))
                                amount = Decimal(str(data.get('v', '0')))
                                filled = Decimal(str(data.get('ev', '0')))
                                
                                log_payload = {'status': status, 'symbol': symbol, 'side': side}

                                if status in ['wait', 'watch']: # NEW
                                    self.orders_cache[order_id] = {
                                        'id': order_id, 'symbol': symbol, 'side': side,
                                        'price': float(price), 'amount': float(amount), 'filled': float(filled),
                                        'value': float(price * amount), 'quote_currency': self.quote_currency,
                                        'timestamp': data.get('otms'), 'status': status
                                    }
                                    log_payload.update({'price': float(price), 'amount': float(amount)})
                                    await self.app['broadcast_log'](log_payload, self.name)

                                elif status == 'trade': # PARTIALLY_FILLED or FILLED by trade
                                    if order_id in self.orders_cache:
                                        self.orders_cache[order_id]['filled'] = float(filled)
                                        self.orders_cache[order_id]['status'] = status
                                    else: # If order is not in cache, treat it as new
                                        self.orders_cache[order_id] = {
                                            'id': order_id, 'symbol': symbol, 'side': side,
                                            'price': float(price), 'amount': float(amount), 'filled': float(filled),
                                            'value': float(price * amount), 'quote_currency': self.quote_currency,
                                            'timestamp': data.get('otms'), 'status': status
                                        }
                                    
                                    trade_volume = Decimal(str(data.get('v', '0')))
                                    trade_price = Decimal(str(data.get('p', '0')))
                                    log_payload.update({'price': float(trade_price), 'amount': float(trade_volume)})
                                    await self.app['broadcast_log'](log_payload, self.name)

                                    if side == 'buy' and asset in self.balances_cache and trade_volume > 0:
                                        old_total_amount = self.balances_cache[asset].get('total_amount', Decimal('0')) - trade_volume
                                        old_avg_price = self.balances_cache[asset].get('avg_buy_price')
                                        if old_avg_price is None or old_avg_price <= 0 or old_total_amount <= 0:
                                            new_avg_price = trade_price
                                        else:
                                            old_cost = old_total_amount * old_avg_price
                                            fill_cost = trade_volume * trade_price
                                            new_total_amount = old_total_amount + trade_volume
                                            new_avg_price = (old_cost + fill_cost) / new_total_amount if new_total_amount > 0 else trade_price
                                        self.balances_cache[asset]['avg_buy_price'] = new_avg_price
                                        self.logger.info(f"Average price for {asset} updated to {new_avg_price} by trade.")

                                elif status in ['done', 'cancel']: # FILLED or CANCELED
                                    if order_id in self.orders_cache:
                                        del self.orders_cache[order_id]
                                    
                                    if status == 'done':
                                        log_payload.update({'price': float(price), 'amount': float(amount)})

                                    await self.app['broadcast_log'](log_payload, self.name)

                                await self.app['broadcast_orders_update'](self)
                                await self.update_subscriptions_if_needed()

                        except (json.JSONDecodeError, UnicodeDecodeError):
                            self.logger.warning(f"Received unprocessable message from Upbit: {message}")

            except ConnectionClosed:
                self.logger.warning("Upbit User Data Stream connection closed. Reconnecting in 5 seconds...")
                self.wscp = None
                await asyncio.sleep(5)
            except Exception as e:
                self.logger.error(f"Error in Upbit User Data Stream: {e}", exc_info=True)
                self.wscp = None
                await asyncio.sleep(5)

    async def update_subscriptions_if_needed(self) -> None:
        lock = self.app.get('subscription_lock')
        if not lock:
            return

        async with lock:
            holding_assets = set(self.balances_cache.keys())
            order_assets = {o['symbol'].split('/')[0] for o in self.orders_cache.values() if o.get('symbol')}
            required_assets = (holding_assets | order_assets)

            if required_assets != self.tracked_assets:
                self.logger.info(f"Subscription update required. Old: {self.tracked_assets}, New: {required_assets}")
                self.tracked_assets = required_assets
                
                websocket = self.price_ws
                if websocket and websocket.state == State.OPEN:
                    await self._send_price_subscription(websocket)

    async def cancel_order(self, order_id: str, symbol: str) -> None:
        try:
            await self.exchange.cancel_order(order_id, symbol)
            self.logger.info(f"Successfully sent cancel request for order {order_id}")
            await self.app['broadcast_log']({'status': 'Cancelling', 'symbol': symbol, 'order_id': order_id}, self.name)
            if order_id in self.orders_cache:
                del self.orders_cache[order_id]
            # The websocket event for order cancellation will trigger the subscription update.
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
        # The websocket events for order cancellations will trigger the subscription update.

    async def close(self) -> None:
        await self.exchange.close()