from ccxt.async_support import binance
from decimal import Decimal
import asyncio
import os
import websockets
import json
import logging
from aiohttp import web

# 로깅 설정
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 콘솔 핸들러
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)



clients = set()
async def broadcast_message(message):
    """모든 연결된 클라이언트에게 메시지를 전송합니다."""
    for ws in list(clients):
        try:
            await ws.send_json(message)
        except ConnectionResetError:
            # 클라이언트 연결이 이미 끊어진 경우 무시
            logger.warning(f"Failed to send message to a disconnected client.")

async def broadcast_orders_update():
    """모든 클라이언트에게 현재 주문 목록을 전송합니다."""
    update_message = {'type': 'orders_update', 'data': list(orders_cache.values())}
    await broadcast_message(update_message)
def create_balance_update_message(symbol, balance_data):
    """잔고 정보로부터 클라이언트에게 보낼 업데이트 메시지를 생성합니다."""
    price = Decimal(str(balance_data.get('price', '0')))
    free_amount = balance_data.get('free', Decimal('0'))
    locked_amount = balance_data.get('locked', Decimal('0'))
    total_amount = free_amount + locked_amount
    value = price * total_amount
    avg_buy_price = balance_data.get('avg_buy_price') # 평단가 정보 추가

    return {
        'symbol': symbol,
        'price': float(price),
        'free': float(free_amount),
        'locked': float(locked_amount),
        'value': float(value),
        'avg_buy_price': float(avg_buy_price) if avg_buy_price is not None else None,
        'quote_currency': 'USDT'
    }


balances_cache = {}
orders_cache = {} # To cache open orders
async def handle_websocket(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    # 상세한 클라이언트 접속 정보를 직접 로깅합니다.
    remote_ip = request.remote
    user_agent = request.headers.get('User-Agent', '-')
    logger.info(
        f'Client connected: {remote_ip} - "GET {request.path} HTTP/1.1" 101 - "{user_agent}"'
    )
    
    clients.add(ws)
    logger.info(f"Total clients: {len(clients)}")

    try:
        # Send initial holdings data
        if balances_cache:
            for symbol, data in balances_cache.items():
                update_message = create_balance_update_message(symbol, data)
                await ws.send_json(update_message)
        
        # Send initial open orders data
        if orders_cache:
            update_message = {'type': 'orders_update', 'data': list(orders_cache.values())}
            try:
                await ws.send_json(update_message) # Note: orders_update data should also contain quote_currency
            except ConnectionResetError:
                logger.warning("Failed to send initial 'orders_update' to a newly connected client.")

        # on_shutdown에서 연결이 닫히면 이 루프는 자동으로 종료됩니다.
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get('type')
                    binance_exchange = request.app.get('binance_exchange')

                    if not binance_exchange:
                        logger.error("Exchange is not initialized. Cannot process order cancellation.")
                        continue

                    if msg_type == 'cancel_orders':
                        orders_to_cancel = data.get('orders', [])
                        logger.info(f"Received request to cancel {len(orders_to_cancel)} orders.")
                        for order in orders_to_cancel:
                            try:
                                await binance_exchange.cancel_order(order['id'], order['symbol'])
                                logger.info(f"Successfully sent cancel request for order {order['id']}")
                                # Optimistically remove from cache and trigger subscription update
                                if order['id'] in orders_cache:
                                    del orders_cache[order['id']]
                                await update_subscriptions_if_needed(request.app)
                            except Exception as e:
                                logger.error(f"Failed to cancel order {order['id']}: {e}")
                        
                        # Send updated list to all clients
                        await broadcast_orders_update()
                    
                    elif msg_type == 'cancel_all_orders':
                        logger.info("Received request to cancel all orders.")
                        all_orders = list(orders_cache.values())
                        if not all_orders:
                            logger.info("No open orders to cancel.")
                            continue
                        
                        for order in all_orders:
                             try:
                                await binance_exchange.cancel_order(order['id'], order['symbol'])
                                logger.info(f"Successfully sent cancel request for order {order['id']}")
                             except Exception as e:
                                logger.error(f"Failed to cancel order {order['id']}: {e}")
                        
                        # Clear cache and send update
                        orders_cache.clear()
                        await broadcast_orders_update()

                except json.JSONDecodeError:
                    logger.warning(f"Received non-JSON message: {msg.data}")
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f'ws connection closed with exception {ws.exception()}')
    except asyncio.CancelledError:
        logger.info(f"Websocket handler for {request.remote} cancelled.")
    finally:
        clients.discard(ws)
        logger.info(f"Client disconnected: {request.remote}. Total clients: {len(clients)}")
    return ws

async def binance_data_fetcher(app):
    """
    바이낸스 가격 정보 웹소켓에 연결하고, 메시지를 처리합니다.
    """
    price_ws_ready = app.get('price_ws_ready')
    url = "wss://stream.binance.com:9443/ws"
    while True:
        try:
            async with websockets.connect(url) as websocket:
                app['price_ws'] = websocket
                logger.info("Price data websocket connection established.")
                if price_ws_ready:
                    price_ws_ready.set()

                initial_assets = app.get('tracked_assets', set())
                if initial_assets:
                    # USDT는 가격 구독에서 제외
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
                        symbol = data['s'].replace('USDT', '')
                        price = data['c']
                        logger.debug(f"Price received: {symbol} = {price}")

                        # 실제로 보유한 자산인 경우 amount, value 포함
                        if symbol in balances_cache:
                            balances_cache[symbol]['price'] = Decimal(price)
                            update_message = create_balance_update_message(symbol, balances_cache[symbol])
                        # 미체결 주문에만 있는 자산인 경우 가격 정보만 전송
                        else:
                            update_message = {'symbol': symbol, 'price': float(Decimal(price)), 'quote_currency': 'USDT'}

                        await broadcast_message(update_message)
                    elif 'result' in data:
                        logger.info(f"Subscription response received: {data}")

        except (websockets.ConnectionClosed, websockets.ConnectionClosedError):
            logger.warning("Price data websocket connection closed. Reconnecting in 5 seconds...")
            if price_ws_ready:
                price_ws_ready.clear()
            app['price_ws'] = None
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"An error occurred in binance_data_fetcher: {e}", exc_info=True)
            if price_ws_ready:
                price_ws_ready.clear()
            app['price_ws'] = None
            await asyncio.sleep(5)


async def http_handler(request):
    """
    HTTP 요청을 처리하여 정적 파일을 제공합니다.
    """
    filename = request.match_info.get('filename', 'index.html')
    filepath = os.path.join(os.path.dirname(__file__), filename)
    if os.path.exists(filepath):
        return web.FileResponse(filepath)
    return web.Response(status=404)

import aiohttp

async def update_subscriptions_if_needed(app):
    """필요에 따라 가격 정보 구독을 업데이트합니다."""
    lock = app.get('subscription_lock')
    if not lock:
        return

    async with lock:
        websocket = app.get('price_ws')
        if not websocket or websocket.state != websockets.protocol.State.OPEN:
            logger.warning("Price websocket not available for subscription update.")
            return

        async def send_subscription_message(method, assets):
            if not assets:
                return
            streams = [f"{asset.lower()}usdt@miniTicker" for asset in assets]
            message = json.dumps({"method": method, "params": streams, "id": int(asyncio.get_running_loop().time())})
            await websocket.send(message)
            logger.info(f"Sent {method} for: {streams}")

        holding_assets = set(balances_cache.keys())
        order_assets = {order['symbol'].replace('USDT', '').replace('/', '') for order in orders_cache.values()}
        required_assets = (holding_assets | order_assets)
        
        current_assets = app.get('tracked_assets', set())
        
        to_add = required_assets - current_assets
        to_remove = current_assets - required_assets

        # USDT는 가격 구독에서 제외
        await send_subscription_message("SUBSCRIBE", [asset for asset in to_add if asset != 'USDT'])
        await send_subscription_message("UNSUBSCRIBE", [asset for asset in to_remove if asset != 'USDT'])

        app['tracked_assets'] = required_assets
        if to_add or to_remove:
            logger.info(f"Subscription updated. Added: {to_add}, Removed: {to_remove}. Current: {required_assets}")

async def get_listen_key(exchange):
    """바이낸스에서 현물 User Data Stream을 위한 listen key를 받아옵니다."""
    listen_url = 'https://api.binance.com/api/v3/userDataStream'
    headers = {'X-MBX-APIKEY': exchange.apiKey}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(listen_url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as response:
                response.raise_for_status()
                data = await response.json()
                logger.info("Successfully obtained listen key.")
                return data['listenKey']
    except Exception as e:
        logger.error(f"Failed to get listen key: {e}")
        return None

async def keepalive_listen_key(exchange, listen_key):
    """Listen key를 30분마다 갱신합니다."""
    listen_url = 'https://api.binance.com/api/v3/userDataStream'
    headers = {'X-MBX-APIKEY': exchange.apiKey}
    while True:
        try:
            await asyncio.sleep(1800)  # 30분
            async with aiohttp.ClientSession() as session:
                async with session.put(listen_url, headers=headers, params={'listenKey': listen_key}, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    response.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to keep listen key alive: {e}")
            # If keepalive fails, we might need to get a new key. For now, we just log and break.
            break

async def user_data_stream_fetcher(app, listen_key):
    """User Data Stream에 연결하여 계정 업데이트를 수신합니다."""
    price_ws_ready = app.get('price_ws_ready')
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
                        for balance_update in data['B']:
                            asset = balance_update['a']
                            free_amount = Decimal(balance_update['f'])
                            locked_amount = Decimal(balance_update['l'])
                            total_amount = free_amount + locked_amount

                            is_existing = asset in balances_cache
                            is_positive_total = total_amount > Decimal('0')

                            if is_positive_total:
                                old_free = balances_cache.get(asset, {}).get('free', Decimal('0'))
                                old_locked = balances_cache.get(asset, {}).get('locked', Decimal('0'))
                                has_changed = (free_amount != old_free) or (locked_amount != old_locked)

                                if not is_existing:
                                    logger.info(f"New asset detected: {asset}, free: {free_amount}, locked: {locked_amount}")
                                    balances_cache[asset] = {'price': 0}
                                
                                balances_cache[asset]['free'] = free_amount
                                balances_cache[asset]['locked'] = locked_amount
                                
                                if has_changed:
                                    logger.info(f"Balance for {asset} updated. Free: {free_amount}, Locked: {locked_amount}")
                                    # 평단가 재계산 또는 기존 값 유지
                                    if 'avg_buy_price' not in balances_cache[asset]:
                                         balances_cache[asset]['avg_buy_price'] = await calculate_average_buy_price(app['binance_exchange'], asset, total_amount)
                                    
                                    update_message = create_balance_update_message(asset, balances_cache[asset])
                                    await broadcast_message(update_message)

                                if asset == 'USDT' and balances_cache[asset].get('price', 0) == 0:
                                    balances_cache[asset]['price'] = 1.0
                                
                                if not is_existing:
                                    await update_subscriptions_if_needed(app)

                            elif not is_positive_total and is_existing:
                                logger.info(f"Asset sold out or zeroed: {asset}")
                                del balances_cache[asset]
                                await broadcast_message({'type': 'remove_holding', 'symbol': asset})
                                await update_subscriptions_if_needed(app)
                    
                    elif event_type == 'executionReport':
                        order_id = data['i']
                        symbol = data['s']
                        status = data['X']
                        
                        if status in ['NEW', 'PARTIALLY_FILLED']:
                            price = Decimal(data['p'])
                            amount = Decimal(data['q'])
                            orders_cache[order_id] = {
                                'id': order_id,
                                'symbol': symbol,
                                'side': data['S'],
                                'price': float(price),
                                'amount': float(amount),
                                'value': float(price * amount),
                                'quote_currency': symbol[len(data['s'].replace(data['S'], '')):], # Heuristic to get quote
                                'timestamp': data['T'],
                                'status': status
                            }
                            logger.info(f"New/updated order: {order_id} - {symbol} {status}")
                        else:  # CANCELED, FILLED, REJECTED, EXPIRED
                            if order_id in orders_cache:
                                del orders_cache[order_id]
                                logger.info(f"Order {order_id} removed from cache.")
                        
                        # UI 즉시 업데이트
                        await broadcast_orders_update()

                        # 주문 체결 시 평단가 재계산 (최적화)
                        if status == 'FILLED':
                            asset = symbol.replace('USDT', '')
                            if asset in balances_cache and data['S'] == 'BUY': # 매수 체결 시에만 평단가 변경
                                old_total_amount = balances_cache[asset].get('total_amount', Decimal('0'))
                                old_avg_price = balances_cache[asset].get('avg_buy_price', Decimal('0'))
                                
                                filled_amount = Decimal(data['q'])
                                filled_price = Decimal(data['p'])

                                if old_total_amount > 0 and old_avg_price > 0:
                                    old_cost = old_total_amount * old_avg_price
                                    new_cost = filled_amount * filled_price
                                    new_total_amount = old_total_amount + filled_amount
                                    new_avg_price = (old_cost + new_cost) / new_total_amount
                                else: # 기존 보유량이 없던 경우, 첫 매수가 평단가
                                     new_avg_price = filled_price
                                
                                balances_cache[asset]['avg_buy_price'] = new_avg_price
                                logger.info(f"Average price for {asset} updated to {new_avg_price} by new trade.")
                                # 잔고 정보는 outboundAccountPosition 이벤트에서 업데이트되므로 여기서는 평단가만 갱신
                        
                        # 구독 상태 업데이트
                        await update_subscriptions_if_needed(app)

        except websockets.ConnectionClosed:
            logger.warning("User Data Stream connection closed. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Error in User Data Stream fetcher: {e}")
            await asyncio.sleep(5)


async def on_startup(app):
    """
    aiohttp 앱 시작 시 백그라운드 태스크를 생성합니다.
    """
    app['tracked_assets'] = set()
    logger.info("Server starting up...")
    
    # 바이낸스 거래소 인스턴스 생성
    try:
        secrets_path = os.path.join(os.path.dirname(__file__), 'secrets.json')
        with open(secrets_path) as f:
            secrets = json.load(f)
        api_key = secrets['exchanges']['binance']['api_key']
        secret_key = secrets['exchanges']['binance']['secret_key']
        
        if "YOUR_BINANCE" in api_key or "YOUR_BINANCE" in secret_key:
            logger.warning("Please replace placeholder keys in secrets.json with your actual Binance API keys.")
            return

        app['binance_exchange'] = binance({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
                'warnOnFetchOpenOrdersWithoutSymbol': False,
            },
        })
    except (FileNotFoundError, KeyError) as e:
        logger.error(f"Could not initialize Binance exchange due to missing secrets: {e}")
        return

    # 초기 데이터 로드 및 캐싱
    try:
        balance = await app['binance_exchange'].fetch_balance()
        open_orders = await app['binance_exchange'].fetch_open_orders()

        # 미체결 주문 캐싱
        for order in open_orders:
            price = Decimal(str(order.get('price') or 0))
            amount = Decimal(str(order.get('amount') or 0))
            orders_cache[order['id']] = {
                'id': order['id'], 'symbol': order['symbol'], 'side': order['side'],
                'price': float(price), 'amount': float(amount), 'value': float(price * amount),
                'quote_currency': order['symbol'].split('/')[1] if order['symbol'] and '/' in order['symbol'] else 'USDT',
                'timestamp': order['timestamp'], 'status': order['status']
            }
        logger.info(f"Fetched {len(open_orders)} open orders at startup.")

        # 잔고 캐싱 및 평단가 계산
        total_balances = {
            asset: total for asset, total in balance.get('total', {}).items() if total > 0
        }
        for asset, total_amount in total_balances.items():
            avg_buy_price = await calculate_average_buy_price(app['binance_exchange'], asset, total_amount)
            free_amount = Decimal(str(balance.get('free', {}).get(asset, 0)))
            locked_amount = Decimal(str(balance.get('used', {}).get(asset, 0)))
            balances_cache[asset] = {
                'free': free_amount,
                'locked': locked_amount,
                'total_amount': free_amount + locked_amount, # 총 수량 캐싱
                'price': Decimal('1.0') if asset == 'USDT' else Decimal('0'),
                'avg_buy_price': avg_buy_price
            }
            logger.info(f"Asset: {asset}, Avg Buy Price: {avg_buy_price if avg_buy_price is not None else 'N/A'}")

    except Exception as e:
        logger.error(f"Failed to fetch initial data from Binance: {e}")
        await app['binance_exchange'].close()
        return

    # 백그라운드 작업 시작
    holding_assets = set(balances_cache.keys())
    order_assets = {o['symbol'].replace('USDT', '').replace('/', '') for o in orders_cache.values()}
    app['tracked_assets'] = holding_assets | order_assets
    app['price_ws_ready'] = asyncio.Event()
    app['subscription_lock'] = asyncio.Lock()
    
    app['fetcher_task'] = asyncio.create_task(binance_data_fetcher(app))

    listen_key = await get_listen_key(app['binance_exchange'])
    if listen_key:
        app['user_data_stream_task'] = asyncio.create_task(user_data_stream_fetcher(app, listen_key))
        app['keepalive_task'] = asyncio.create_task(keepalive_listen_key(app['binance_exchange'], listen_key))
        logger.info("User data stream and keepalive tasks started.")

async def on_shutdown(app):
    """
    Ctrl+C 수신 시 가장 먼저 실행됩니다. 모든 클라이언트 연결을 종료합니다.
    """
    logger.info("Shutdown signal received. Closing client connections...")
    for ws in list(clients):
        await ws.close(code=1001, message=b'Server shutdown')
    logger.info(f"All {len(clients)} client connections closed.")

async def on_cleanup(app):
    """
    모든 정리가 끝난 후 마지막으로 실행됩니다. 백그라운드 태스크를 취소합니다.
    """
    logger.info("Cleaning up background tasks...")
    tasks_to_cancel = [
        'fetcher_task', 'user_data_stream_task', 'keepalive_task'
    ]
    for task_name in tasks_to_cancel:
        if task_name in app and not app[task_name].done():
            app[task_name].cancel()
            try:
                await app[task_name]
            except asyncio.CancelledError:
                pass  # 작업 취소는 예상된 동작

    if 'binance_exchange' in app:
        await app['binance_exchange'].close()
        logger.info("Binance exchange connection closed.")

    logger.info("All background tasks stopped.")

async def calculate_average_buy_price(exchange, asset, current_amount):
    """
    제공된 알고리즘을 사용하여 자산의 평균 매수 가격을 계산합니다.
    """
    if not exchange:
        logger.error("Exchange object is not initialized. Cannot calculate average buy price.")
        return None
        
    if asset == 'USDT' or current_amount <= 0:
        return None

    symbol = f"{asset}/USDT"
    try:
        trade_history = await exchange.fetch_closed_orders(symbol)
        if not trade_history:
            return None

        amount_to_trace = Decimal(str(current_amount))
        cost = Decimal('0')
        amount_traced = Decimal('0')
        
        # 거래 내역을 최신순으로 순회
        for trade in sorted(trade_history, key=lambda x: x['timestamp'], reverse=True):
            if amount_to_trace <= Decimal('0'):
                break

            filled = Decimal(str(trade['filled']))
            price = Decimal(str(trade['price']))

            if trade['side'] == 'buy':
                buy_amount = min(amount_to_trace, filled)
                cost += buy_amount * price
                amount_traced += buy_amount
                amount_to_trace -= buy_amount
        
        if amount_traced > Decimal('0'):
            # 모든 계산은 Decimal로 하고, 최종 반환값만 유지
            return cost / amount_traced
        return None

    except Exception as e:
        logger.error(f"Error calculating average buy price for {asset}: {e}")
        return None

def init_app():
    """
    aiohttp 애플리케이션을 생성하고 설정합니다.
    """
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)

    app.router.add_get('/ws', handle_websocket)
    app.router.add_get('/', http_handler)
    app.router.add_get('/{filename}', http_handler)
    return app

def main():
    try:
        with open(os.path.join(os.path.dirname(__file__), 'config.json')) as f:
            config = json.load(f)
        host = config.get('host', 'localhost')
        port = config.get('port', 8000)
    except FileNotFoundError:
        host = 'localhost'
        port = 8000
        logger.warning("config.json not found, defaulting to host 'localhost' and port 8000")
    
    app = init_app()
    logger.info(f"Attempting to start server on http://{host}:{port}")
    # access_log=None으로 기본 로거를 비활성화하여 로그 출력 시점 문제를 해결합니다.
    web.run_app(app, host=host, port=port, access_log=None)
    logger.info("Server shutdown complete.")

if __name__ == "__main__":
    main()
