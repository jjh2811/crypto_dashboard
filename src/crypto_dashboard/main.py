from ccxt.async_support import binance
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

async def get_binance_balance():
    """
    바이낸스 계정의 잔고 정보를 가져옵니다.
    """
    try:
        secrets_path = os.path.join(os.path.dirname(__file__), 'secrets.json')
        with open(secrets_path) as f:
            secrets = json.load(f)
        api_key = secrets['exchanges']['binance']['api_key']
        secret_key = secrets['exchanges']['binance']['secret_key']
    except FileNotFoundError:
        logger.warning("secrets.json file not found. Skipping balance fetch.")
        return {}
    except KeyError:
        logger.warning("Could not find binance api_key or secret_key in secrets.json. Skipping balance fetch.")
        return {}

    if "YOUR_BINANCE" in api_key or "YOUR_BINANCE" in secret_key:
        logger.warning("Please replace placeholder keys in secrets.json with your actual Binance API keys.")
        return {}

    exchange = binance({
        'apiKey': api_key,
        'secret': secret_key,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'spot',
        },
    })

    try:
        balance = await exchange.fetch_balance()
        positive_balances = {
            asset: amount
            for asset, amount in balance['total'].items()
            if isinstance(amount, (int, float)) and amount > 0
        }
        logger.info(f"Fetched balances: {positive_balances}")
        return positive_balances
    except Exception as e:
        logger.error(f"An error occurred while fetching balance: {e}")
        return None
    finally:
        await exchange.close()


clients = set()
balances_cache = {}
orders_cache = {} # To cache open orders
binance_exchange = None # Binance exchange instance

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
                price = data.get('price', 0)
                amount = data.get('amount', 0)
                value = float(price) * float(amount)
                await ws.send_json({'symbol': symbol, 'amount': amount, 'price': price, 'value': value})
        
        # Send initial open orders data
        if orders_cache:
            update_message = {'type': 'orders_update', 'data': list(orders_cache.values())}
            try:
                await ws.send_json(update_message)
            except ConnectionResetError:
                logger.warning("Failed to send initial 'orders_update' to a newly connected client.")

        # on_shutdown에서 연결이 닫히면 이 루프는 자동으로 종료됩니다.
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get('type')

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
                                # Optimistically remove from cache and notify clients
                                if order['id'] in orders_cache:
                                    del orders_cache[order['id']]
                            except Exception as e:
                                logger.error(f"Failed to cancel order {order['id']}: {e}")
                        
                        # Send updated list to all clients
                        update_message = {'type': 'orders_update', 'data': list(orders_cache.values())}
                        for client_ws in list(clients):
                            try:
                                await client_ws.send_json(update_message)
                            except ConnectionResetError:
                                logger.warning("Failed to send 'orders_update' after cancellation.")
                    
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
                        update_message = {'type': 'orders_update', 'data': []}
                        for client_ws in list(clients):
                            try:
                                await client_ws.send_json(update_message)
                            except ConnectionResetError:
                                logger.warning("Failed to send 'orders_update' after cancelling all.")

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
                    streams = [f"{asset.lower()}usdt@miniTicker" for asset in initial_assets]
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
                            balances_cache[symbol]['price'] = price
                            amount = balances_cache[symbol].get('amount', 0)
                            value = float(price) * float(amount)
                            update_message = {'symbol': symbol, 'price': price, 'amount': amount, 'value': value}
                        # 미체결 주문에만 있는 자산인 경우 가격 정보만 전송
                        else:
                            update_message = {'symbol': symbol, 'price': price}

                        for ws in list(clients):
                            try:
                                await ws.send_json(update_message)
                            except ConnectionResetError:
                                pass
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
    required_assets = (holding_assets | order_assets) - {'USDT'}
    
    current_assets = app.get('tracked_assets', set())
    
    to_add = required_assets - current_assets
    to_remove = current_assets - required_assets

    await send_subscription_message("SUBSCRIBE", list(to_add))
    await send_subscription_message("UNSUBSCRIBE", list(to_remove))

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
                    logger.info("Listen key kept alive.")
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
                        for balance in data['B']:
                            asset = balance['a']
                            free_amount = float(balance['f'])
                            
                            is_existing = asset in balances_cache
                            is_positive = free_amount > 0

                            if is_positive and not is_existing:
                                logger.info(f"New asset detected: {asset}, amount: {free_amount}")
                                balances_cache[asset] = {'amount': free_amount, 'price': 0}
                                await update_subscriptions_if_needed(app)

                            elif not is_positive and is_existing:
                                logger.info(f"Asset sold out: {asset}")
                                del balances_cache[asset]
                                for ws in list(clients):
                                    try:
                                        await ws.send_json({'type': 'remove_holding', 'symbol': asset})
                                    except ConnectionResetError:
                                        logger.warning("Failed to send 'remove_holding' message to a client.")
                                await update_subscriptions_if_needed(app)
                    
                    elif event_type == 'executionReport':
                        order_id = data['i']
                        symbol = data['s']
                        status = data['X']
                        
                        if status in ['NEW', 'PARTIALLY_FILLED']:
                            price = float(data['p'])
                            amount = float(data['q'])
                            orders_cache[order_id] = {
                                'id': order_id,
                                'symbol': symbol,
                                'side': data['S'],
                                'price': price,
                                'amount': amount,
                                'value': price * amount,
                                'timestamp': data['T'],
                                'status': status
                            }
                            logger.info(f"New/updated order: {order_id} - {symbol} {status}")
                        else:  # CANCELED, FILLED, REJECTED, EXPIRED
                            if order_id in orders_cache:
                                del orders_cache[order_id]
                                logger.info(f"Order {order_id} removed from cache.")
                        
                        # UI 즉시 업데이트
                        update_message = {'type': 'orders_update', 'data': list(orders_cache.values())}
                        for ws in list(clients):
                            try:
                                await ws.send_json(update_message)
                            except ConnectionResetError:
                                logger.warning("Failed to send 'orders_update' to a client.")
                        
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
    global binance_exchange
    app['tracked_assets'] = set()
    logger.info("Server starting up...")
    
    # 바이낸스 거래소 인스턴스 생성
    try:
        secrets_path = os.path.join(os.path.dirname(__file__), 'secrets.json')
        with open(secrets_path) as f:
            secrets = json.load(f)
        api_key = secrets['exchanges']['binance']['api_key']
        secret_key = secrets['exchanges']['binance']['secret_key']
        
        binance_exchange = binance({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
                'warnOnFetchOpenOrdersWithoutSymbol': False,
            },
        })

        # 초기 잔고 가져오기
        initial_balances = await get_binance_balance()
        if initial_balances:
            for asset, amount in initial_balances.items():
                balances_cache[asset] = {'amount': amount, 'price': 0}

        # 초기 미체결 주문 가져오기
        try:
            open_orders = await binance_exchange.fetch_open_orders()
            for order in open_orders:
                price = float(order.get('price') or 0)
                amount = float(order.get('amount') or 0)
                orders_cache[order['id']] = {
                    'id': order['id'],
                    'symbol': order['symbol'],
                    'side': order['side'],
                    'price': price,
                    'amount': amount,
                    'value': price * amount,
                    'timestamp': order['timestamp'],
                    'status': order['status']
                }
            logger.info(f"Fetched {len(open_orders)} open orders at startup.")
            if open_orders:
                # Send initial orders to clients that might already be connected
                update_message = {'type': 'orders_update', 'data': list(orders_cache.values())}
                for ws in list(clients):
                    try:
                        await ws.send_json(update_message)
                    except ConnectionResetError:
                        logger.warning("Failed to send initial 'orders_update' to a client.")
        except Exception as e:
            logger.error(f"Failed to fetch open orders at startup: {e}")

        # 추적할 초기 자산 목록 설정
        holding_assets = set(balances_cache.keys())
        order_assets = {o['symbol'].replace('USDT', '').replace('/', '') for o in orders_cache.values()}
        app['tracked_assets'] = (holding_assets | order_assets) - {'USDT'}
        app['price_ws_ready'] = asyncio.Event()
        
        # 가격 정보 fetcher 시작
        app['fetcher_task'] = asyncio.create_task(binance_data_fetcher(app))
    
        # User Data Stream 시작
        listen_key = await get_listen_key(binance_exchange)
        if listen_key:
            app['user_data_stream_task'] = asyncio.create_task(user_data_stream_fetcher(app, listen_key))
            app['keepalive_task'] = asyncio.create_task(keepalive_listen_key(binance_exchange, listen_key))
            logger.info("User data stream and keepalive tasks started.")

    except (FileNotFoundError, KeyError) as e:
        logger.error(f"Could not initialize Binance exchange due to missing secrets: {e}")

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
    global binance_exchange
    logger.info("Cleaning up background tasks...")
    if 'fetcher_task' in app:
        app['fetcher_task'].cancel()
    if 'user_data_stream_task' in app:
        app['user_data_stream_task'].cancel()
    if 'keepalive_task' in app:
        app['keepalive_task'].cancel()
    
    tasks = [t for t in [
        app.get('fetcher_task'),
        app.get('user_data_stream_task'),
        app.get('keepalive_task')
    ] if t]

    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass # 작업 취소는 예상된 동작

    if binance_exchange:
        await binance_exchange.close()
        logger.info("Binance exchange connection closed.")

    logger.info("All background tasks stopped.")

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