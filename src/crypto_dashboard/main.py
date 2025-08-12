from decimal import Decimal
import asyncio
import os
import json
import logging
from aiohttp import web

from .binance import BinanceExchange

# 로깅 설정
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 콘솔 핸들러
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)

clients = set()
balances_cache = {}
orders_cache = {}
log_cache = []

async def broadcast_message(message):
    """모든 연결된 클라이언트에게 메시지를 전송합니다."""
    for ws in list(clients):
        try:
            await ws.send_json(message)
        except ConnectionResetError:
            logger.warning(f"Failed to send message to a disconnected client.")

async def broadcast_orders_update():
    """모든 클라이언트에게 현재 주문 목록을 전송합니다."""
    update_message = {'type': 'orders_update', 'data': list(orders_cache.values())}
    await broadcast_message(update_message)

async def broadcast_log(message):
    """모든 클라이언트에게 로그 메시지를 전송합니다."""
    log_message = {'type': 'log', 'message': message}
    log_cache.append(log_message)
    logger.info(f"LOG: {message}")
    await broadcast_message(log_message)

def create_balance_update_message(symbol, balance_data):
    """잔고 정보로부터 클라이언트에게 보낼 업데이트 메시지를 생성합니다."""
    price = Decimal(str(balance_data.get('price', '0')))
    free_amount = balance_data.get('free', Decimal('0'))
    locked_amount = balance_data.get('locked', Decimal('0'))
    total_amount = free_amount + locked_amount
    value = price * total_amount
    avg_buy_price = balance_data.get('avg_buy_price')

    return {
        'symbol': symbol,
        'price': float(price),
        'free': float(free_amount),
        'locked': float(locked_amount),
        'value': float(value),
        'avg_buy_price': float(avg_buy_price) if avg_buy_price is not None else None,
        'quote_currency': 'USDT'
    }

async def handle_websocket(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    logger.info('Client connected.')
    clients.add(ws)
    logger.info(f"Total clients: {len(clients)}")

    try:
        if balances_cache:
            for symbol, data in balances_cache.items():
                update_message = create_balance_update_message(symbol, data)
                await ws.send_json(update_message)
        
        if orders_cache:
            update_message = {'type': 'orders_update', 'data': list(orders_cache.values())}
            try:
                await ws.send_json(update_message)
            except ConnectionResetError:
                logger.warning("Failed to send initial 'orders_update' to a newly connected client.")

        if log_cache:
            for log_msg in log_cache:
                try:
                    await ws.send_json(log_msg)
                except ConnectionResetError:
                    logger.warning("Failed to send cached logs to a newly connected client.")
                    break
        
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get('type')
                    exchange = request.app.get('exchange')

                    if not exchange:
                        logger.error("Exchange is not initialized. Cannot process order cancellation.")
                        continue

                    if msg_type == 'cancel_orders':
                        orders_to_cancel = data.get('orders', [])
                        logger.info(f"Received request to cancel {len(orders_to_cancel)} orders.")
                        for order in orders_to_cancel:
                            await exchange.cancel_order(order['id'], order['symbol'])
                        await broadcast_orders_update()
                    
                    elif msg_type == 'cancel_all_orders':
                        await exchange.cancel_all_orders()
                        await broadcast_orders_update()

                except json.JSONDecodeError:
                    logger.warning(f"Received non-JSON message: {msg.data}")
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f'ws connection closed with exception {ws.exception()}')
    except asyncio.CancelledError:
        logger.info("Websocket handler cancelled.")
    finally:
        clients.discard(ws)
        logger.info(f"Client disconnected. Total clients: {len(clients)}")
    return ws

async def http_handler(request):
    filename = request.match_info.get('filename', 'index.html')
    filepath = os.path.join(os.path.dirname(__file__), filename)
    if os.path.exists(filepath):
        return web.FileResponse(filepath)
    return web.Response(status=404)

async def on_startup(app):
    logger.info("Server starting up...")
    app['balances_cache'] = balances_cache
    app['orders_cache'] = orders_cache
    app['log_cache'] = log_cache
    app['broadcast_message'] = broadcast_message
    app['broadcast_orders_update'] = broadcast_orders_update
    app['broadcast_log'] = broadcast_log
    app['create_balance_update_message'] = create_balance_update_message
    app['tracked_assets'] = set()
    app['price_ws_ready'] = asyncio.Event()
    app['subscription_lock'] = asyncio.Lock()

    try:
        secrets_path = os.path.join(os.path.dirname(__file__), 'secrets.json')
        with open(secrets_path) as f:
            secrets = json.load(f)
        api_key = secrets['exchanges']['binance']['api_key']
        secret_key = secrets['exchanges']['binance']['secret_key']
        
        if "YOUR_BINANCE" in api_key or "YOUR_BINANCE" in secret_key:
            logger.warning("Please replace placeholder keys in secrets.json with your actual Binance API keys.")
            return

        exchange = BinanceExchange(api_key, secret_key, app)
        app['exchange'] = exchange
        await exchange.get_initial_data()

    except (FileNotFoundError, KeyError) as e:
        logger.error(f"Could not initialize Binance exchange due to missing secrets: {e}")
        return
    except Exception as e:
        logger.error(f"Error during exchange initialization: {e}")
        return

    holding_assets = set(balances_cache.keys())
    order_assets = {o['symbol'].replace('USDT', '').replace('/', '') for o in orders_cache.values()}
    app['tracked_assets'] = holding_assets | order_assets

    app['price_ws_task'] = asyncio.create_task(exchange.connect_price_ws())
    listen_key = await exchange.get_listen_key()
    if listen_key:
        app['user_data_ws_task'] = asyncio.create_task(exchange.connect_user_data_ws(listen_key))
        app['keepalive_task'] = asyncio.create_task(exchange.keepalive_listen_key(listen_key))
        logger.info("User data stream and keepalive tasks started.")

async def on_shutdown(app):
    logger.info("Shutdown signal received. Closing client connections...")
    for ws in list(clients):
        await ws.close(code=1001, message=b'Server shutdown')
    logger.info(f"All {len(clients)} client connections closed.")

async def on_cleanup(app):
    logger.info("Cleaning up background tasks...")
    tasks_to_cancel = ['price_ws_task', 'user_data_ws_task', 'keepalive_task']
    for task_name in tasks_to_cancel:
        if task_name in app and not app[task_name].done():
            app[task_name].cancel()
            try:
                await app[task_name]
            except asyncio.CancelledError:
                pass

    if 'exchange' in app:
        await app['exchange'].close()
        logger.info("Exchange connection closed.")

    logger.info("All background tasks stopped.")

def init_app():
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
    web.run_app(app, host=host, port=port, access_log=None)
    logger.info("Server shutdown complete.")

if __name__ == "__main__":
    main()