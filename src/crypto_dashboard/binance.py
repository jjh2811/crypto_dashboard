import asyncio
import json
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional, Protocol, TypedDict, cast

import aiohttp
import websockets
from aiohttp import web
from ccxt.async_support import binance

logger = logging.getLogger(__name__)


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

    async def fetch_balance(self, *args, **kwargs) -> Balances:
        ...

    async def fetch_open_orders(self, *args, **kwargs) -> List[Order]:
        ...

    async def cancel_order(self, order_id: str, symbol: str) -> Any:
        ...
    
    async def fetch_closed_orders(self, *args, **kwargs) -> List[Order]:
        ...

    async def close(self) -> None:
        ...


class BinanceExchange:
    def __init__(self, api_key: str, secret_key: str, app: web.Application) -> None:
        self._exchange = binance({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
                'warnOnFetchOpenOrdersWithoutSymbol': False,
            },
        })
        self.app = app
        self.balances_cache: Dict[str, Dict[str, Any]] = app['balances_cache']
        self.orders_cache: Dict[str, Dict[str, Any]] = app['orders_cache']

    @property
    def exchange(self) -> ExchangeProtocol:
        return cast(ExchangeProtocol, self._exchange)

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
                        'quote_currency': order.get('symbol', '').split('/')[1] if order.get('symbol') and '/' in order.get('symbol', '') else 'USDT',
                        'timestamp': order.get('timestamp'), 'status': order.get('status')
                    }
            logger.info(f"Fetched {len(open_orders)} open orders at startup.")

            total_balances = {
                asset: total for asset, total in balance.get('total', {}).items() if total > 0
            }
            for asset, total_amount in total_balances.items():
                avg_buy_price = await self.calculate_average_buy_price(asset, Decimal(str(total_amount)))
                free_amount = Decimal(str(balance.get('free', {}).get(asset, 0)))
                locked_amount = Decimal(str(balance.get('used', {}).get(asset, 0)))
                self.balances_cache[asset] = {
                    'free': free_amount,
                    'locked': locked_amount,
                    'total_amount': free_amount + locked_amount,
                    'price': Decimal('1.0') if asset == 'USDT' else Decimal('0'),
                    'avg_buy_price': avg_buy_price
                }
                logger.info(f"Asset: {asset}, Avg Buy Price: {avg_buy_price if avg_buy_price is not None else 'N/A'}")

        except Exception as e:
            logger.error(f"Failed to fetch initial data from Binance: {e}")
            await self.exchange.close()
            raise

    async def calculate_average_buy_price(self, asset: str, current_amount: Decimal) -> Optional[Decimal]:
        if asset == 'USDT' or current_amount <= 0:
            return None

        symbol = f"{asset}/USDT"
        try:
            trade_history = await self.exchange.fetch_closed_orders(symbol=symbol)
            if not trade_history:
                return None

            amount_to_trace = Decimal(str(current_amount))
            cost = Decimal('0')
            amount_traced = Decimal('0')
            
            for trade in sorted(trade_history, key=lambda x: x.get('timestamp', 0), reverse=True):
                if amount_to_trace <= Decimal('0'):
                    break

                filled = Decimal(str(trade.get('filled', '0')))
                price = Decimal(str(trade.get('price', '0')))

                if trade.get('side') == 'buy':
                    buy_amount = min(amount_to_trace, filled)
                    cost += buy_amount * price
                    amount_traced += buy_amount
                    amount_to_trace -= buy_amount
            
            if amount_traced > Decimal('0'):
                return cost / amount_traced
            return None

        except Exception as e:
            logger.error(f"Error calculating average buy price for {asset}: {e}")
            return None

    async def get_listen_key(self) -> Optional[str]:
        listen_url = 'https://api.binance.com/api/v3/userDataStream'
        headers = {'X-MBX-APIKEY': self.exchange.apiKey}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(listen_url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    response.raise_for_status()
                    data = await response.json()
                    logger.info("Successfully obtained listen key.")
                    return data.get('listenKey')
        except Exception as e:
            logger.error(f"Failed to get listen key: {e}")
            return None

    async def keepalive_listen_key(self, listen_key: str) -> None:
        listen_url = 'https://api.binance.com/api/v3/userDataStream'
        headers = {'X-MBX-APIKEY': self.exchange.apiKey}
        while True:
            try:
                await asyncio.sleep(1800)  # 30분
                async with aiohttp.ClientSession() as session:
                    async with session.put(listen_url, headers=headers, params={'listenKey': listen_key}, timeout=aiohttp.ClientTimeout(total=5)) as response:
                        response.raise_for_status()
            except Exception as e:
                logger.error(f"Failed to keep listen key alive: {e}")
                break

    async def connect_price_ws(self) -> None:
        url = "wss://stream.binance.com:9443/ws"
        while True:
            try:
                async with websockets.connect(url) as websocket:
                    self.app['price_ws'] = websocket
                    logger.info("Price data websocket connection established.")
                    if self.app.get('price_ws_ready'):
                        self.app['price_ws_ready'].set()

                    initial_assets = self.app.get('tracked_assets', set())
                    if initial_assets:
                        assets_to_subscribe = [asset for asset in initial_assets if asset != 'USDT']
                        streams = [f"{asset.lower()}usdt@miniTicker" for asset in assets_to_subscribe]
                        if streams:
                            await websocket.send(json.dumps({
                                "method": "SUBSCRIBE",
                                "params": streams,
                                "id": 1
                            }))
                            logger.info(f"Initial subscription sent for: {streams}")

                    async for message in websocket:
                        data = json.loads(message)
                        if data.get('e') == '24hrMiniTicker':
                            symbol = data.get('s', '').replace('USDT', '')
                            price = data.get('c')
                            if not symbol or not price:
                                continue
                            logger.debug(f"Price received: {symbol} = {price}")

                            if symbol in self.balances_cache:
                                self.balances_cache[symbol]['price'] = Decimal(price)
                                update_message = self.app['create_balance_update_message'](symbol, self.balances_cache[symbol])
                            else:
                                update_message = {'symbol': symbol, 'price': float(Decimal(price)), 'quote_currency': 'USDT'}

                            await self.app['broadcast_message'](update_message)
                        elif 'result' in data:
                            logger.info(f"Subscription response received: {data}")

            except (websockets.ConnectionClosed, websockets.ConnectionClosedError):
                logger.warning("Price data websocket connection closed. Reconnecting in 5 seconds...")
                if self.app.get('price_ws_ready'):
                    self.app['price_ws_ready'].clear()
                self.app['price_ws'] = None
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"An error occurred in connect_price_ws: {e}", exc_info=True)
                if self.app.get('price_ws_ready'):
                    self.app['price_ws_ready'].clear()
                self.app['price_ws'] = None
                await asyncio.sleep(5)

    async def connect_user_data_ws(self, listen_key: str) -> None:
        price_ws_ready = self.app.get('price_ws_ready')
        if price_ws_ready:
            await price_ws_ready.wait()
        url = f"wss://stream.binance.com:9443/ws/{listen_key}"
        logger.info(f"Connecting to Binance User Data Stream: {url}")
        while True:
            try:
                async with websockets.connect(url) as websocket:
                    logger.info("Binance User Data Stream connection established.")
                    while True:
                        message = await websocket.recv()
                        data = json.loads(message)
                        event_type = data.get('e')

                        if event_type == 'outboundAccountPosition':
                            for balance_update in data.get('B', []):
                                asset = balance_update.get('a')
                                if not asset:
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
                                        logger.info(f"New asset detected: {asset}, free: {free_amount}, locked: {locked_amount}")
                                        self.balances_cache[asset] = {'price': 0}
                                    
                                    self.balances_cache[asset]['free'] = free_amount
                                    self.balances_cache[asset]['locked'] = locked_amount
                                    
                                    if has_changed:
                                        logger.info(f"Balance for {asset} updated. Free: {free_amount}, Locked: {locked_amount}")
                                        if 'avg_buy_price' not in self.balances_cache[asset]:
                                             self.balances_cache[asset]['avg_buy_price'] = await self.calculate_average_buy_price(asset, total_amount)
                                        
                                        update_message = self.app['create_balance_update_message'](asset, self.balances_cache[asset])
                                        await self.app['broadcast_message'](update_message)

                                    if asset == 'USDT' and self.balances_cache[asset].get('price', 0) == 0:
                                        self.balances_cache[asset]['price'] = 1.0
                                    
                                    if not is_existing:
                                        await self.update_subscriptions_if_needed()

                                elif not is_positive_total and is_existing:
                                    logger.info(f"Asset sold out or zeroed: {asset}")
                                    del self.balances_cache[asset]
                                    await self.app['broadcast_message']({'type': 'remove_holding', 'symbol': asset})
                                    await self.update_subscriptions_if_needed()
                        
                        elif event_type == 'executionReport':
                            order_id = data.get('i')
                            symbol = data.get('s')
                            status = data.get('X')
                            if not all([order_id, symbol, status]):
                                continue
                            
                            if status in ['NEW', 'PARTIALLY_FILLED']:
                                price = Decimal(data.get('p', '0'))
                                amount = Decimal(data.get('q', '0'))
                                self.orders_cache[order_id] = {
                                    'id': order_id,
                                    'symbol': symbol,
                                    'side': data.get('S'),
                                    'price': float(price),
                                    'amount': float(amount),
                                    'value': float(price * amount),
                                    'quote_currency': symbol[len(symbol.replace(data.get('S', ''), '')):],
                                    'timestamp': data.get('T'),
                                    'status': status
                                }
                                logger.info(f"New/updated order: {order_id} - {symbol} {status}")
                            else:
                                if order_id in self.orders_cache:
                                    del self.orders_cache[order_id]
                                    logger.info(f"Order {order_id} removed from cache.")
                            
                            await self.app['broadcast_orders_update']()

                            if status == 'FILLED':
                                asset = symbol.replace('USDT', '')
                                if asset in self.balances_cache and data.get('S') == 'BUY':
                                    old_total_amount = self.balances_cache[asset].get('total_amount', Decimal('0'))
                                    old_avg_price = self.balances_cache[asset].get('avg_buy_price', Decimal('0'))
                                    
                                    filled_amount = Decimal(data.get('q', '0'))
                                    filled_price = Decimal(data.get('p', '0'))

                                    if old_total_amount > 0 and old_avg_price > 0:
                                        old_cost = old_total_amount * old_avg_price
                                        new_cost = filled_amount * filled_price
                                        new_total_amount = old_total_amount + filled_amount
                                        new_avg_price = (old_cost + new_cost) / new_total_amount
                                    else:
                                         new_avg_price = filled_price
                                    
                                    self.balances_cache[asset]['avg_buy_price'] = new_avg_price
                                    logger.info(f"Average price for {asset} updated to {new_avg_price} by new trade.")
                            
                            await self.update_subscriptions_if_needed()

            except websockets.ConnectionClosed:
                logger.warning("User Data Stream connection closed. Reconnecting in 5 seconds...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Error in User Data Stream fetcher: {e}")
                await asyncio.sleep(5)

    async def update_subscriptions_if_needed(self) -> None:
        lock = self.app.get('subscription_lock')
        if not lock:
            return

        async with lock:
            websocket = self.app.get('price_ws')
            if not websocket or websocket.state != websockets.protocol.State.OPEN:
                logger.warning("Price websocket not available for subscription update.")
                return

            async def send_subscription_message(method: str, assets: List[str]) -> None:
                if not assets:
                    return
                streams = [f"{asset.lower()}usdt@miniTicker" for asset in assets]
                message = json.dumps({"method": method, "params": streams, "id": int(asyncio.get_running_loop().time())})
                await websocket.send(message)
                logger.info(f"Sent {method} for: {streams}")

            holding_assets = set(self.balances_cache.keys())
            order_assets = {order.get('symbol', '').replace('USDT', '').replace('/', '') for order in self.orders_cache.values()}
            required_assets = (holding_assets | order_assets)
            
            current_assets = self.app.get('tracked_assets', set())
            
            to_add = required_assets - current_assets
            to_remove = current_assets - required_assets

            await send_subscription_message("SUBSCRIBE", [asset for asset in to_add if asset != 'USDT'])
            await send_subscription_message("UNSUBSCRIBE", [asset for asset in to_remove if asset != 'USDT'])

            self.app['tracked_assets'] = required_assets
            if to_add or to_remove:
                logger.info(f"Subscription updated. Added: {to_add}, Removed: {to_remove}. Current: {required_assets}")

    async def cancel_order(self, order_id: str, symbol: str) -> None:
        try:
            await self.exchange.cancel_order(order_id, symbol)
            logger.info(f"Successfully sent cancel request for order {order_id}")
            if order_id in self.orders_cache:
                del self.orders_cache[order_id]
            await self.update_subscriptions_if_needed()
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")

    async def cancel_all_orders(self) -> None:
        logger.info("Received request to cancel all orders.")
        all_orders = list(self.orders_cache.values())
        if not all_orders:
            logger.info("No open orders to cancel.")
            return
        
        for order in all_orders:
            order_id = order.get('id')
            symbol = order.get('symbol')
            if not order_id or not symbol:
                continue
            try:
                await self.exchange.cancel_order(order_id, symbol)
                logger.info(f"Successfully sent cancel request for order {order_id}")
            except Exception as e:
                logger.error(f"Failed to cancel order {order_id}: {e}")
        
        self.orders_cache.clear()

    async def close(self) -> None:
        await self.exchange.close()