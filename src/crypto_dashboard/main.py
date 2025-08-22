from decimal import Decimal
import asyncio
import os
import json
import logging
import importlib
from datetime import datetime, timezone
from aiohttp import web
import secrets


SECRET_TOKEN = secrets.token_hex(32)


# 로깅 설정
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 콘솔 핸들러
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)

clients = set()
log_cache = []

MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_TIME = 600  # 10 minutes
login_attempts = {}
last_login_attempt = {}

async def broadcast_message(message):
    """모든 연결된 클라이언트에게 메시지를 전송합니다."""
    for ws in list(clients):
        try:
            await ws.send_json(message)
        except ConnectionResetError:
            logger.warning(f"Failed to send message to a disconnected client.")

async def broadcast_orders_update(exchange):
    """모든 클라이언트에게 현재 주문 목록을 전송합니다."""
    update_message = {'type': 'orders_update', 'data': list(exchange.orders_cache.values())}
    await broadcast_message(update_message)

async def broadcast_log(message, exchange_name=None):
    """모든 클라이언트에게 로그 메시지를 전송합니다."""
    log_message = {
        'type': 'log',
        'message': message,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'exchange': exchange_name
    }
    log_cache.append(log_message)
    logger.info(f"LOG: {message}")
    await broadcast_message(log_message)

async def login(request):
    """POST 요청 + 비밀번호 입력 후 쿠키 발급"""
    ip_address = request.transport.get_extra_info('peername')[0]
    current_time = datetime.now(timezone.utc).timestamp()
    error_message = ""
    button_disabled = ""

    login_path = os.path.join(os.path.dirname(__file__), 'login.html')
    try:
        with open(login_path, 'r') as f:
            login_html = f.read()
    except FileNotFoundError:
        return web.Response(text="Login page not found.", status=404)

    if ip_address in last_login_attempt and current_time - last_login_attempt[ip_address] < LOGIN_LOCKOUT_TIME:
        if login_attempts.get(ip_address, 0) >= MAX_LOGIN_ATTEMPTS:
            error_message = "너무 많은 로그인 시도를 했습니다. 잠시 후 다시 시도하세요."
            button_disabled = "disabled"
            return web.Response(text=login_html.replace("{{error_message}}", error_message).replace("{{button_disabled}}", button_disabled), status=429, content_type='text/html')

    if request.method == "POST":
        data = await request.post()
        password = data.get("password", "")
        if password != request.app['login_password']:
            login_attempts[ip_address] = login_attempts.get(ip_address, 0) + 1
            last_login_attempt[ip_address] = current_time
            if login_attempts[ip_address] >= MAX_LOGIN_ATTEMPTS:
                error_message = "너무 많은 로그인 시도를 했습니다. 잠시 후 다시 시도하세요."
                button_disabled = "disabled"
                return web.Response(text=login_html.replace("{{error_message}}", error_message).replace("{{button_disabled}}", button_disabled), status=429, content_type='text/html')
            else:
                error_message = "비밀번호가 틀렸습니다."
                return web.Response(text=login_html.replace("{{error_message}}", error_message).replace("{{button_disabled}}", ""), status=401, content_type='text/html')

        login_attempts.pop(ip_address, None)
        last_login_attempt.pop(ip_address, None)

        resp = web.HTTPFound('/')
        resp.set_cookie(
            "auth_token",
            SECRET_TOKEN,
            httponly=True,
            secure=True,
            samesite="Strict",
            max_age=86400
        )
        return resp

    # GET request
    return web.Response(text=login_html.replace("{{error_message}}", "").replace("{{button_disabled}}", ""), status=200, content_type='text/html')

async def logout(request):
    """쿠키 삭제로 로그아웃"""
    resp = web.HTTPFound('/login')
    resp.del_cookie("auth_token")
    return resp

@web.middleware
async def auth_middleware(request, handler):
    # 로그인/로그아웃 페이지 및 정적 파일은 예외
    if request.path in ("/login", "/logout", "/style.css", "/script.js"):
        return await handler(request)

    token = request.cookies.get("auth_token")
    if token != SECRET_TOKEN:
        return web.HTTPFound('/login')
    return await handler(request)


async def handle_websocket(request):
    app = request.app
    token = request.cookies.get("auth_token")
    if token != SECRET_TOKEN:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.close(code=1008, message=b'Authentication failed')
        return ws

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    logger.info('Client connected.')
    clients.add(ws)
    logger.info(f"Total clients: {len(clients)}")

    try:
        exchange_names = list(app['exchanges'].keys())
        await ws.send_json({'type': 'exchanges_list', 'data': exchange_names})

        if app['reference_prices'] and app['reference_time']:
            await ws.send_json({
                'type': 'reference_price_info',
                'time': app['reference_time'],
                'prices': app['reference_prices']
            })

        for exchange in app['exchanges'].values():
            for symbol, data in exchange.balances_cache.items():
                update_message = exchange.create_balance_update_message(symbol, data)
                await ws.send_json(update_message)
            
            if exchange.orders_cache:
                update_message = {'type': 'orders_update', 'data': list(exchange.orders_cache.values())}
                try:
                    await ws.send_json(update_message)
                except ConnectionResetError:
                    logger.warning(f"Failed to send initial 'orders_update' to a newly connected client for {exchange.name}.")

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
                    
                    exchanges = app.get('exchanges', {})
                    if not exchanges:
                        logger.error("No exchanges are initialized. Cannot process order cancellation.")
                        continue
                    
                    # TODO: Add exchange selection logic for multi-exchange support
                    exchange = list(exchanges.values())[0]

                    if msg_type == 'cancel_orders':
                        orders_to_cancel = data.get('orders', [])
                        logger.info(f"Received request to cancel {len(orders_to_cancel)} orders on {exchange.name}.")
                        for order in orders_to_cancel:
                            await exchange.cancel_order(order['id'], order['symbol'])
                        await broadcast_orders_update(exchange)
                    
                    elif msg_type == 'cancel_all_orders':
                        await exchange.cancel_all_orders()
                        await broadcast_orders_update(exchange)

                except json.JSONDecodeError:
                    logger.warning(f"Received non-JSON message: {msg.data}")
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f'ws connection closed with exception {ws.exception()}')
    except asyncio.CancelledError:
        logger.info("Websocket handler cancelled.")
    finally:
        clients.discard(ws)
        logger.info(f"Client disconnected. Total clients: {len(clients)}")
        if not clients:
            logger.info("Last client disconnected. Storing current prices as reference.")
            app['reference_prices'] = {}
            for exchange_name, exchange in app['exchanges'].items():
                exchange_reference_prices = {
                    symbol: float(data['price']) 
                    for symbol, data in exchange.balances_cache.items() 
                    if 'price' in data and symbol != exchange.quote_currency
                }
                if exchange_reference_prices:
                    app['reference_prices'][exchange_name] = exchange_reference_prices
            
            if app['reference_prices']:
                app['reference_time'] = datetime.now(timezone.utc).isoformat()
                logger.info(f"Reference prices saved at {app['reference_time']} for {list(app['reference_prices'].keys())}")
            else:
                app['reference_time'] = None
                logger.info("No assets to track for reference pricing.")
    return ws

async def http_handler(request):
    filename = request.match_info.get('filename', 'index.html')
    filepath = os.path.join(os.path.dirname(__file__), filename)
    if os.path.exists(filepath):
        return web.FileResponse(filepath)
    return web.Response(status=404)

async def on_startup(app):
    logger.info("Server starting up...")
    app['log_cache'] = log_cache
    app['broadcast_message'] = broadcast_message
    app['broadcast_orders_update'] = broadcast_orders_update
    app['broadcast_log'] = broadcast_log
    app['tracked_assets'] = set()
    app['price_ws_ready'] = asyncio.Event()
    app['subscription_lock'] = asyncio.Lock()
    app['exchanges'] = {}
    app['exchange_tasks'] = []
    app['reference_prices'] = {}
    app['reference_time'] = None

    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    with open(config_path) as f:
        config = json.load(f)

    secrets_path = os.path.join(os.path.dirname(__file__), 'secrets.json')
    with open(secrets_path) as f:
        secrets_data = json.load(f)

    login_password = secrets_data.get('login_password')
    if not login_password:
        logger.error("Login password not found in secrets.json under 'login_password' key. Please add it.")
        os._exit(1)
    app['login_password'] = login_password

    exchanges_config = config.get('exchanges', {})
    if not exchanges_config:
        logger.error("No exchanges configured in config.json")
        return

    for exchange_name, exchange_config in exchanges_config.items():
        try:
            logger.info(f"Initializing exchange: {exchange_name}")
            
            testnet = exchange_config.get('testnet', {}).get('use', False)
            api_key_section = f"{exchange_name}_testnet" if testnet else exchange_name

            if api_key_section not in secrets_data.get('exchanges', {}):
                logger.error(f"API keys for '{api_key_section}' not found in secrets.json")
                continue

            api_key = secrets_data['exchanges'][api_key_section]['api_key']
            secret_key = secrets_data['exchanges'][api_key_section]['secret_key']

            if f"YOUR_{api_key_section.upper()}" in api_key or f"YOUR_{api_key_section.upper()}" in secret_key:
                logger.warning(f"Please replace placeholder keys in secrets.json for {api_key_section}.")
                continue

            module_name = f".{exchange_name}"
            class_name = f"{exchange_name.capitalize()}Exchange"
            
            module = importlib.import_module(module_name, package=__package__)
            exchange_class = getattr(module, class_name)

            exchange_instance = exchange_class(api_key, secret_key, app, exchange_name)
            await exchange_instance.get_initial_data()

            app['exchanges'][exchange_name] = exchange_instance

            price_ws_task = asyncio.create_task(exchange_instance.connect_price_ws())
            user_data_ws_task = asyncio.create_task(exchange_instance.connect_user_data_ws())
            app['exchange_tasks'].extend([price_ws_task, user_data_ws_task])
            
            logger.info(f"Successfully initialized and connected to {exchange_name}.")

        except (FileNotFoundError, KeyError) as e:
            logger.error(f"Could not initialize {exchange_name} exchange due to missing secrets or config: {e}")
            continue
        except (ModuleNotFoundError, AttributeError) as e:
            logger.error(f"Could not load exchange module for '{exchange_name}': {e}")
            continue
        except Exception as e:
            logger.error(f"Error during {exchange_name} exchange initialization: {e}")
            continue

    # This logic might need adjustment for multi-exchange asset tracking
    all_tracked_assets = set()
    for exchange in app['exchanges'].values():
        holding_assets = set(exchange.balances_cache.keys())
        order_assets = {o['symbol'].replace(exchange.quote_currency, '').replace('/', '') for o in exchange.orders_cache.values()}
        all_tracked_assets.update(holding_assets | order_assets)
    app['tracked_assets'] = all_tracked_assets
    logger.info("User data stream task started.")

async def on_shutdown(app):
    logger.info("Shutdown signal received. Closing client connections...")
    for ws in list(clients):
        await ws.close(code=1001, message=b'Server shutdown')
    logger.info(f"All {len(clients)} client connections closed.")

async def on_cleanup(app):
    logger.info("Cleaning up background tasks...")
    if 'exchange_tasks' in app:
        for task in app['exchange_tasks']:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    if 'exchanges' in app:
        for exchange_name, exchange in app['exchanges'].items():
            await exchange.close()
            logger.info(f"{exchange_name} exchange connection closed.")

    logger.info("All background tasks stopped.")

def init_app():
    app = web.Application(middlewares=[auth_middleware])
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)

    app.router.add_get('/ws', handle_websocket)
    app.router.add_get('/', http_handler)
    app.router.add_get('/{filename}', http_handler)
    app.router.add_get('/login', login)
    app.router.add_post('/login', login)
    app.router.add_get('/logout', logout)
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