import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import importlib
import json
import logging
import os
import secrets

from aiohttp import web

from .nlptrade import TradeCommand, format_trade_command_for_confirmation

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
    orders_with_exchange = []
    for order in exchange.orders_cache.values():
        order_copy = order.copy()
        order_copy['exchange'] = exchange.name
        orders_with_exchange.append(order_copy)
    update_message = {'type': 'orders_update', 'data': orders_with_exchange}
    await broadcast_message(update_message)

async def broadcast_log(message, exchange_name=None, exchange_logger=None):
    """모든 클라이언트에게 로그 메시지를 전송합니다."""
    log_message = {
        'type': 'log',
        'message': message,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'exchange': exchange_name
    }
    log_cache.append(log_message)

    # Use exchange-specific logger if available, otherwise use root logger
    log_logger = exchange_logger if exchange_logger else logger
    log_logger.info(f"LOG: {message}")

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
        ws = web.WebSocketResponse(heartbeat=25)
        await ws.prepare(request)
        await ws.close(code=1008, message=b'Authentication failed')
        return ws

    ws = web.WebSocketResponse(heartbeat=25)
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
                orders_with_exchange = []
                for order in exchange.orders_cache.values():
                    order_copy = order.copy()
                    order_copy['exchange'] = exchange.name
                    orders_with_exchange.append(order_copy)
                update_message = {'type': 'orders_update', 'data': orders_with_exchange}
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
                    exchange_name = data.get('exchange')

                    if not exchange_name or exchange_name not in exchanges:
                        logger.error(f"Invalid exchange specified or no exchanges initialized. Exchange: {exchange_name}")
                        if msg_type in ('cancel_orders', 'cancel_all_orders', 'nlp_command', 'nlp_execute'):
                            continue

                    exchange = exchanges.get(exchange_name)

                    if msg_type == 'cancel_orders':
                        orders_to_cancel = data.get('orders', [])
                        logger.info(f"Received request to cancel {len(orders_to_cancel)} orders on {exchange.name}.")
                        for order in orders_to_cancel:
                            await exchange.cancel_order(order['id'], order['symbol'])

                    elif msg_type == 'cancel_all_orders':
                        logger.info(f"Received request to cancel all orders on {exchange.name}.")
                        await exchange.cancel_all_orders()

                    elif msg_type == 'nlp_command':
                        text = data.get('text', '')
                        if not exchange or not exchange.parser:
                            logger.error(f"NLP parser not available for exchange: {exchange_name}")
                            await ws.send_json({'type': 'nlp_error', 'message': f'{exchange_name}의 자연어 처리기가 준비되지 않았습니다.'})
                            continue
                        
                        result = await exchange.parser.parse(text)
                        if isinstance(result, TradeCommand):
                            confirmation_message = format_trade_command_for_confirmation(result)
                            await ws.send_json({
                                'type': 'nlp_trade_confirm',
                                'confirmation_message': confirmation_message,
                                'command': asdict(result)
                            })
                        elif isinstance(result, str):
                            await ws.send_json({'type': 'nlp_error', 'message': result})
                        else:
                            await ws.send_json({'type': 'nlp_error', 'message': '명령을 해석하지 못했습니다.'})

                    elif msg_type == 'nlp_execute':
                        command_data = data.get('command')
                        if not exchange or not exchange.executor:
                            logger.error(f"NLP executor not available for exchange: {exchange_name}")
                            await ws.send_json({'type': 'nlp_error', 'message': f'{exchange_name}의 거래 실행기가 준비되지 않았습니다.'})
                            continue

                        if command_data:
                            trade_command = TradeCommand(**command_data)
                            logger.info(f"Executing NLP command: {trade_command}")
                            result = await exchange.executor.execute(trade_command)
                            await broadcast_log(result, exchange.name, exchange.logger)
                        else:
                            logger.error("No command data received for nlp_execute")
                            await ws.send_json({'type': 'nlp_error', 'message': '거래 실행 정보가 없습니다.'})

                except json.JSONDecodeError:
                    logger.warning(f"Received non-JSON message: {msg.data}")
                except Exception as e:
                    logger.error(f"Error processing websocket message: {e}", exc_info=True)

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

    init_tasks = []
    pending_exchanges = []

    for exchange_name, exchange_config in exchanges_config.items():
        try:
            logger.info(f"Preparing to initialize exchange: {exchange_name}")

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

            init_tasks.append(exchange_instance.get_initial_data())
            pending_exchanges.append(exchange_instance)

        except (FileNotFoundError, KeyError) as e:
            logger.error(f"Could not prepare {exchange_name} exchange due to missing secrets or config: {e}")
        except (ModuleNotFoundError, AttributeError) as e:
            logger.error(f"Could not load exchange module for '{exchange_name}': {e}")
        except Exception as e:
            logger.error(f"Error during {exchange_name} exchange preparation: {e}")

    if not init_tasks:
        logger.warning("No exchanges were prepared for initialization.")
        return

    logger.info(f"Initializing {len(init_tasks)} exchanges concurrently...")
    results = await asyncio.gather(*init_tasks, return_exceptions=True)

    for instance, result in zip(pending_exchanges, results):
        exchange_name = instance.name
        if isinstance(result, Exception):
            logger.error(f"Error during {exchange_name} exchange initialization: {result}")
        else:
            app['exchanges'][exchange_name] = instance
            price_ws_task = asyncio.create_task(instance.connect_price_ws())
            user_data_ws_task = asyncio.create_task(instance.connect_user_data_ws())
            app['exchange_tasks'].extend([price_ws_task, user_data_ws_task])

            await instance.price_ws_connected_event.wait()
            if hasattr(instance, 'logon_successful_event'):
                await instance.logon_successful_event.wait()
            await instance.user_data_subscribed_event.wait()
            logger.info(f"Successfully initialized and connected to {exchange_name}.")

    logger.info("All exchange initializations complete.")

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
