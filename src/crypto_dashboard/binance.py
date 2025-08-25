import asyncio
import base64
from decimal import Decimal
import json
import logging
import time
import os
from typing import Any, Dict, List, Optional, cast

from aiohttp import web
from ccxt.async_support import binance
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from websockets.exceptions import ConnectionClosed, ConnectionClosedError
from websockets.legacy.client import WebSocketClientProtocol, connect
from websockets.protocol import State

from .exchange_utils import calculate_average_buy_price
from .protocols import ExchangeProtocol


class BinanceExchange:
    def __init__(self, api_key: str, secret_key: str, app: web.Application, exchange_name: str) -> None:
        self.name = exchange_name
        self.logger = logging.getLogger(exchange_name)
        with open(os.path.join(os.path.dirname(__file__), 'config.json')) as f:
            config = json.load(f)
        
        binance_config = config['exchanges']['binance']
        testnet_config = binance_config.get('testnet', {})
        self.testnet = testnet_config.get('use', False)
        self.whitelist = testnet_config.get('whitelist', []) if self.testnet else []
        self.quote_currency = binance_config.get('quote_currency', 'USDT')

        if self.testnet:
            self.price_ws_url = testnet_config['price_ws_url']
            self.user_data_ws_url = testnet_config['user_data_ws_url']
        else:
            self.price_ws_url = binance_config['price_ws_url']
            self.user_data_ws_url = binance_config['user_data_ws_url']

        self._exchange = binance({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
                'warnOnFetchOpenOrdersWithoutSymbol': False,
            },
        })

        if self.testnet:
            self._exchange.set_sandbox_mode(True)

        self.app = app
        self.balances_cache: Dict[str, Dict[str, Any]] = {}
        self.orders_cache: Dict[str, Dict[str, Any]] = {}
        self.userdata_ws: Optional[WebSocketClientProtocol] = None
        self.price_ws: Optional[WebSocketClientProtocol] = None
        self.price_ws_ready = asyncio.Event()
        self.tracked_assets = set()
        self._ws_id_counter = 1

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

            if self.testnet:
                original_assets = list(total_balances.keys())
                total_balances = {
                    asset: total for asset, total in total_balances.items() if asset in self.whitelist
                }
                self.logger.info(f"Testnet mode: Filtering balances with whitelist {self.whitelist}. Kept: {list(total_balances.keys())} from {original_assets}")

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
            self.logger.error(f"Failed to fetch initial data from Binance: {e}")
            await self.exchange.close()
            raise
        
        holding_assets = set(self.balances_cache.keys())
        order_assets = {o['symbol'].replace(self.quote_currency, '').replace('/', '') for o in self.orders_cache.values()}
        self.tracked_assets = holding_assets | order_assets

    async def _logon(self, userdata_ws: WebSocketClientProtocol) -> None:
        try:
            private_key = serialization.load_pem_private_key(
                self.exchange.secret.encode('utf-8'),
                password=None
            )
            if not isinstance(private_key, ed25519.Ed25519PrivateKey):
                raise TypeError("The provided key is not an Ed25519 private key.")

            timestamp = str(int(time.time() * 1000))
            payload = f"apiKey={self.exchange.apiKey}&timestamp={timestamp}"
            signature = base64.b64encode(private_key.sign(payload.encode('utf-8'))).decode('utf-8')

            logon_request = {
                "id": "logon_request",
                "method": "session.logon",
                "params": {
                    "apiKey": self.exchange.apiKey,
                    "timestamp": timestamp,
                    "signature": signature,
                },
            }
            await userdata_ws.send(json.dumps(logon_request))
            self.logger.info("Logon request sent.")
        except Exception as e:
            self.logger.error(f"Error during logon: {e}", exc_info=True)
            raise

    async def _subscribe(self, userdata_ws: WebSocketClientProtocol) -> None:
        subscribe_request = {
            "id": "subscribe_request",
            "method": "userDataStream.subscribe",
            "params": {}
        }
        await userdata_ws.send(json.dumps(subscribe_request))
        self.logger.info("User data stream subscribe request sent.")

    async def connect_price_ws(self) -> None:
        while True:
            try:
                async with connect(self.price_ws_url) as websocket:
                    self.price_ws = websocket
                    self.logger.info("Price data websocket connection established.")
                    self.price_ws_ready.set()

                    if self.tracked_assets:
                        assets_to_subscribe = [asset for asset in self.tracked_assets if asset != 'USDT']
                        streams = [f"{asset.lower()}usdt@miniTicker" for asset in assets_to_subscribe]
                        if streams:
                            await websocket.send(json.dumps({
                                "method": "SUBSCRIBE",
                                "params": streams,
                                "id": 1
                            }))
                            self.logger.info(f"Initial subscription sent for: {streams}")

                    async for message in websocket:
                        data = json.loads(message)
                        if data.get('e') == '24hrMiniTicker':
                            symbol = data.get('s', '').replace(self.quote_currency, '')
                            price = data.get('c')
                            if not symbol or not price:
                                continue
                            self.logger.debug(f"Price received: {symbol} = {price}")

                            if symbol in self.balances_cache:
                                self.balances_cache[symbol]['price'] = Decimal(price)
                                update_message = self.create_balance_update_message(symbol, self.balances_cache[symbol])
                            else:
                                update_message = {'symbol': symbol, 'price': float(Decimal(price))}
                            
                            await self.app['broadcast_message'](update_message)
                        elif 'result' in data and data.get('result') is None:
                            self.logger.info(f"Subscription response received: {data}")

            except (ConnectionClosed, ConnectionClosedError):
                self.logger.warning("Price data websocket connection closed. Reconnecting in 5 seconds...")
                self.price_ws_ready.clear()
                self.price_ws = None
                await asyncio.sleep(5)
            except Exception as e:
                self.logger.error(f"An error occurred in connect_price_ws: {e}", exc_info=True)
                self.price_ws_ready.clear()
                self.price_ws = None
                await asyncio.sleep(5)

    async def connect_user_data_ws(self) -> None:
        await self.price_ws_ready.wait()

        self.logger.info(f"Connecting to Binance User Data Stream: {self.user_data_ws_url}")

        while True:
            try:
                async with connect(self.user_data_ws_url) as websocket:
                    self.userdata_ws = websocket
                    await self._logon(websocket)

                    while True:
                        message = await websocket.recv()
                        raw_data = json.loads(message)

                        if 'id' in raw_data:
                            data = raw_data
                            if data['id'] == 'logon_request':
                                if data.get('status') == 200:
                                    self.logger.info("Logon successful.")
                                    await self._subscribe(websocket)
                                else:
                                    self.logger.error(f"Logon failed: {data}")
                                    break
                            elif data['id'] == 'subscribe_request':
                                if data.get('status') == 200:
                                    self.logger.info("User data stream subscription successful.")
                                else:
                                    self.logger.error(f"Subscription failed: {data}")
                                    break
                            continue
                        
                        if 'event' not in raw_data:
                            self.logger.warning(f"Received message without 'event' field: {raw_data}")
                            continue
                        
                        data = raw_data['event']
                        event_type = data.get('e')

                        if event_type == 'outboundAccountPosition':
                            for balance_update in data.get('B', []):
                                asset = balance_update.get('a')
                                if not asset:
                                    continue

                                if self.testnet and asset not in self.whitelist:
                                    continue

                                free_amount = Decimal(balance_update.get('f', '0'))
                                locked_amount = Decimal(balance_update.get('l', '0'))
                                total_amount = free_amount + locked_amount

                                is_existing = asset in self.balances_cache
                                is_positive_total = total_amount > Decimal('0')

                                if is_positive_total:
                                    old_free = self.balances_cache.get(asset, {}).get('free', Decimal('0'))
                                    old_locked = self.balances_cache.get(asset, {}).get('locked', Decimal('0'))
                                    has_changed = (free_amount != old_free) or (locked_amount != old_locked)

                                    if not is_existing:
                                        self.logger.info(f"New asset detected: {asset}, free: {free_amount}, locked: {locked_amount}")
                                        self.balances_cache[asset] = {'price': 0}

                                    self.balances_cache[asset]['free'] = free_amount
                                    self.balances_cache[asset]['locked'] = locked_amount
                                    self.balances_cache[asset]['total_amount'] = total_amount

                                    if has_changed:
                                        self.logger.info(f"Balance for {asset} updated. Free: {free_amount}, Locked: {locked_amount}")
                                        if 'avg_buy_price' not in self.balances_cache[asset]:
                                             avg_buy_price, realised_pnl = await calculate_average_buy_price(self.exchange, asset, total_amount, self.quote_currency, self.logger)
                                             self.balances_cache[asset]['avg_buy_price'] = avg_buy_price
                                             self.balances_cache[asset]['realised_pnl'] = realised_pnl

                                        update_message = self.create_balance_update_message(asset, self.balances_cache[asset])
                                        await self.app['broadcast_message'](update_message)

                                    if asset == self.quote_currency and self.balances_cache[asset].get('price', 0) == 0:
                                        self.balances_cache[asset]['price'] = 1.0

                                    if not is_existing:
                                        await self.update_subscriptions_if_needed()

                                elif not is_positive_total and is_existing:
                                    self.logger.info(f"Asset sold out or zeroed: {asset}")
                                    del self.balances_cache[asset]
                                    await self.app['broadcast_message']({'type': 'remove_holding', 'symbol': asset})
                                    await self.update_subscriptions_if_needed()

                        elif event_type == 'executionReport':
                            order_id = data.get('i')
                            symbol = data.get('s')
                            status = data.get('X')
                            if not all([order_id, symbol, status]):
                                continue

                            price = Decimal(data.get('p', '0'))
                            original_amount = Decimal(data.get('q', '0'))
                            last_executed_quantity = Decimal(data.get('l', '0'))
                            cumulative_filled_quantity = Decimal(data.get('z', '0'))
                            side = data.get('S')
                            asset = symbol.replace(self.quote_currency, '')


                            log_payload: Dict[str, Any] = {
                                'status': status,
                                'symbol': symbol,
                                'side': side,
                            }

                            if status == 'NEW':
                                self.orders_cache[order_id] = {
                                    'id': order_id,
                                    'symbol': symbol,
                                    'side': side,
                                    'price': float(price),
                                    'amount': float(original_amount),
                                    'filled': float(cumulative_filled_quantity),
                                    'value': float(price * original_amount),
                                    'quote_currency': self.quote_currency,
                                    'timestamp': data.get('T'),
                                    'status': status
                                }
                                self.logger.info(f"New order: {order_id} - {symbol} {status}")
                                log_payload.update({'price': float(price), 'amount': float(original_amount)})
                                await self.app['broadcast_log'](log_payload, self.name)

                            elif status == 'PARTIALLY_FILLED':
                                if order_id in self.orders_cache:
                                    self.orders_cache[order_id]['filled'] = float(cumulative_filled_quantity)
                                    self.orders_cache[order_id]['status'] = status
                                    self.logger.info(f"Updated order: {order_id} - {symbol} {status}, Filled: {cumulative_filled_quantity}")
                                else: # If order is not in cache, treat it as new
                                    self.orders_cache[order_id] = {
                                        'id': order_id,
                                        'symbol': symbol,
                                        'side': side,
                                        'price': float(price),
                                        'amount': float(original_amount),
                                        'filled': float(cumulative_filled_quantity),
                                        'value': float(price * original_amount),
                                        'quote_currency': self.quote_currency,
                                        'timestamp': data.get('T'),
                                        'status': status
                                    }
                                    self.logger.info(f"New (partially filled) order: {order_id} - {symbol} {status}")

                                log_payload.update({'price': float(price), 'amount': float(last_executed_quantity)})
                                await self.app['broadcast_log'](log_payload, self.name)

                            elif status in ['FILLED', 'CANCELED', 'EXPIRED', 'REJECTED']:
                                if order_id in self.orders_cache:
                                    del self.orders_cache[order_id]
                                    self.logger.info(f"Order {order_id} ({symbol} {status}) removed from cache.")
                                
                                if status == 'FILLED':
                                    log_payload.update({'price': float(data.get('L', '0')), 'amount': float(last_executed_quantity)})

                                await self.app['broadcast_log'](log_payload, self.name)

                            await self.app['broadcast_orders_update'](self)

                            if status in ['PARTIALLY_FILLED', 'FILLED'] and side == 'BUY':
                                last_filled_price = Decimal(data.get('L', '0'))

                                if asset in self.balances_cache and last_executed_quantity > 0:
                                    old_total_amount = self.balances_cache[asset].get('total_amount', Decimal('0'))
                                    old_avg_price = self.balances_cache[asset].get('avg_buy_price')

                                    if old_avg_price is None or old_avg_price <= 0 or old_total_amount <= 0:
                                        new_avg_price = last_filled_price
                                    else:
                                        old_cost = old_total_amount * old_avg_price
                                        fill_cost = last_executed_quantity * last_filled_price
                                        new_total_amount = old_total_amount + last_executed_quantity

                                        if new_total_amount > 0:
                                            new_avg_price = (old_cost + fill_cost) / new_total_amount
                                        else:
                                            new_avg_price = last_filled_price
                                    
                                    self.balances_cache[asset]['avg_buy_price'] = new_avg_price
                                    self.balances_cache[asset]['total_amount'] = old_total_amount + last_executed_quantity
                                    self.logger.info(f"Average price for {asset} updated to {new_avg_price} by trade. Last fill: {last_executed_quantity} @ {last_filled_price}")

                            await self.update_subscriptions_if_needed()

            except ConnectionClosed:
                self.logger.warning("User Data Stream connection closed. Reconnecting in 5 seconds...")
                self.userdata_ws = None
                await asyncio.sleep(5)
            except Exception as e:
                self.logger.error(f"Error in User Data Stream fetcher: {e}", exc_info=True)
                self.userdata_ws = None
                await asyncio.sleep(5)

    async def update_subscriptions_if_needed(self) -> None:
        lock = self.app.get('subscription_lock')
        if not lock:
            return

        async with lock:
            websocket = self.price_ws
            if not websocket or websocket.state != State.OPEN:
                self.logger.warning("Price websocket not available for subscription update.")
                return

            async def send_subscription_message(method: str, assets: List[str]) -> None:
                if not assets:
                    return
                streams = [f"{asset.lower()}usdt@miniTicker" for asset in assets]
                request_id = self._ws_id_counter
                self._ws_id_counter += 1
                message = json.dumps({"method": method, "params": streams, "id": request_id})
                await websocket.send(message)
                self.logger.info(f"Sent {method} for: {streams} with ID: {request_id}")

            holding_assets = set(self.balances_cache.keys())
            order_assets = {order.get('symbol', '').replace(self.quote_currency, '').replace('/', '') for order in self.orders_cache.values()}
            required_assets = (holding_assets | order_assets)

            to_add = required_assets - self.tracked_assets
            to_remove = self.tracked_assets - required_assets

            await send_subscription_message("SUBSCRIBE", [asset for asset in to_add if asset != self.quote_currency])
            await send_subscription_message("UNSUBSCRIBE", [asset for asset in to_remove if asset != self.quote_currency])

            self.tracked_assets = required_assets
            if to_add or to_remove:
                self.logger.info(f"Subscription updated. Added: {to_add}, Removed: {to_remove}. Current: {required_assets}")

    async def cancel_order(self, order_id: str, symbol: str) -> None:
        try:
            await self.exchange.cancel_order(order_id, symbol)
            self.logger.info(f"Successfully sent cancel request for order {order_id}")
            await self.app['broadcast_log']({'status': 'Cancelling', 'symbol': symbol, 'order_id': order_id}, self.name)
            # The websocket event will trigger the cache update and subscription update.
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

        # The websocket events will trigger the cache updates and subscription updates.
        self.orders_cache.clear() # Clearing cache immediately for UI responsiveness

    async def close(self) -> None:
        await self.exchange.close()