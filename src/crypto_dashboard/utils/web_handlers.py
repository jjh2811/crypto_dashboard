"""
웹 핸들러 모듈
HTTP 및 WebSocket 핸들러들을 제공합니다.
"""
import asyncio
from dataclasses import asdict
import json
import os
import mimetypes

from aiohttp import web

from ..models.trade_models import TradeCommand
from .text_utils import sanitize_input


async def http_handler(request):
    """HTTP 파일 요청 핸들러"""
    filename = request.match_info.get('filename', 'index.html')
    filepath = os.path.join(os.path.dirname(__file__), '..', 'frontend', filename)
    if os.path.exists(filepath):
        # 파일 확장자에 따라 MIME 타입 추론
        mime_type, _ = mimetypes.guess_type(filepath)
        if mime_type is None:
            # 추론 실패 시 기본값 설정 (예: text/plain)
            mime_type = 'application/octet-stream' # 또는 'text/plain'
        
        # .js 파일에 대해 명시적으로 application/javascript 설정
        if filename.endswith('.js'):
            mime_type = 'application/javascript'
        
        return web.FileResponse(filepath, headers={'Content-Type': mime_type})
    return web.Response(status=404)


async def handle_websocket(request):
    """WebSocket 연결 핸들러"""
    from .broadcast import get_clients

    app = request.app
    clients = get_clients()
    exchanges = {}  # 초기화

    token = request.cookies.get("auth_token")
    from .auth import get_secret_token
    expected_token = get_secret_token()
    if token != expected_token:
        ws = web.WebSocketResponse(heartbeat=25)
        await ws.prepare(request)
        await ws.close(code=1008, message=b'Authentication failed')
        return ws

    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)

    import logging
    logger = logging.getLogger("web")
    logger.info('Client connected.')
    clients.add(ws)
    logger.info(f"Total clients: {len(clients)}")

    try:
        exchanges = app['exchanges']
        exchange_names = list(exchanges.keys())
        await ws.send_json({'type': 'exchanges_list', 'data': exchange_names})

        # Reference price info - 처음 접속시에만 전송 (가격 상대비율 계산용)
        if app['reference_prices'] and app['reference_time']:
            await ws.send_json({
                'type': 'reference_price_info',
                'time': app['reference_time'],
                'prices': app['reference_prices']
            })
        for exchange_name, exchange in exchanges.items():
            # Fetch and send initial prices via REST for all tracked assets at once
            try:
                tracked_symbols = exchange.price_manager.get_tracked_symbols()
                if tracked_symbols:
                    tickers = await exchange.exchange.fetch_tickers(symbols=tracked_symbols)
                    for symbol, ticker in tickers.items():
                        price = ticker.get('last')
                        if price is not None:
                            await ws.send_json({
                                'type': 'price_update',
                                'exchange': exchange_name,
                                'symbol': symbol,
                                'price': float(price)
                            })
            except Exception as e:
                logger.error(f"Failed to fetch initial tickers for {exchange_name}: {e}")

            # 초기에 follow 코인 목록 및 포맷 설정 전송
            follow_message = {
                'type': 'tracked_coins',
                'exchange': exchange_name,
                'follows': list(getattr(exchange, 'follows', []))
            }
            await ws.send_json(follow_message)

            # value_decimal_places 설정 전송
            exchanges_config = app.get('config', {}).get('exchanges', {})
            decimal_places = exchanges_config.get(exchange_name, {}).get('value_decimal_places', 3)
            format_message = {
                'type': 'value_format',
                'exchange': exchange_name,
                'value_decimal_places': decimal_places,
                'quote_currency': exchange.quote_currency
            }
            await ws.send_json(format_message)

            # 잔고 데이터 전송
            for symbol, data in exchange.balance_manager.balances_cache.items():
                update_message = exchange.balance_manager.create_portfolio_update_message(symbol, data)
                await ws.send_json(update_message)

            # 주문 데이터 전송
            if exchange.order_manager.orders_cache:
                orders_with_exchange = []
                for order in exchange.order_manager.orders_cache.values():
                    order_copy = order.copy()
                    order_copy['exchange'] = exchange_name
                    orders_with_exchange.append(order_copy)
                update_message = {'type': 'orders_update', 'data': orders_with_exchange}
                try:
                    await ws.send_json(update_message)
                except ConnectionResetError:
                    logger.warning(f"Failed to send initial 'orders_update' to a newly connected client for {exchange_name}.")

        # 캐시된 로그 전송
        from .broadcast import get_log_cache
        log_cache = get_log_cache()
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

                    if not exchanges:
                        logger.error("No exchanges initialized.")
                        continue

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
                        await app['broadcast_orders_update'](exchange)

                    elif msg_type == 'cancel_all_orders':
                        logger.info(f"Received request to cancel all orders on {exchange.name}.")
                        await exchange.cancel_all_orders()

                    elif msg_type == 'nlp_command':
                        raw_text = data.get('text', '')
                        text = sanitize_input(raw_text)
                        if not text:
                            await ws.send_json({'type': 'nlp_error', 'message': '잘못된 입력입니다.'})
                            continue

                        if not exchange or not exchange.is_nlp_ready():
                            logger.error(f"NLP not ready for exchange: {exchange_name}")
                            await ws.send_json({'type': 'nlp_error', 'message': f'{exchange_name}의 자연어 처리기가 준비되지 않았습니다.'})
                            continue

                        result = await exchange.nlp_trade_manager.parse_command(text)
                        if isinstance(result, TradeCommand):
                            await ws.send_json({
                                'type': 'nlp_trade_confirm',
                                'command': asdict(result)
                            })
                        elif isinstance(result, str):
                            await ws.send_json({'type': 'nlp_error', 'message': result})
                        else:
                            await ws.send_json({'type': 'nlp_error', 'message': '명령을 해석하지 못했습니다.'})

                    elif msg_type == 'nlp_execute':
                        command_data = data.get('command')
                        if not exchange or not exchange.is_nlp_ready():
                            logger.error(f"NLP not ready for exchange: {exchange_name}")
                            await ws.send_json({'type': 'nlp_error', 'message': f'{exchange_name}의 거래 실행기가 준비되지 않았습니다.'})
                            continue

                        if command_data:
                            trade_command = TradeCommand(**command_data)
                            result = await exchange.nlp_trade_manager.execute_command(trade_command)

                            # 실행 결과 확인 후 에러 시 프론트엔드로 전송
                            await app['broadcast_log'](result, exchange.name, exchange.logger)

                            if result.get('status') == 'error':
                                error_message = result.get('message', '거래 실행 중 알 수 없는 에러가 발생했습니다.')
                                await ws.send_json({'type': 'nlp_error', 'message': f'[{exchange_name.upper()}] {error_message}'})

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
            for exchange_name, exchange in exchanges.items():
                exchange_reference_prices = {
                    symbol: float(data['price'])
                    for symbol, data in exchange.balance_manager.balances_cache.items()
                    if 'price' in data and symbol != getattr(exchange, 'quote_currency', 'USDT')
                }
                if exchange_reference_prices:
                    app['reference_prices'][exchange_name] = exchange_reference_prices

            if app['reference_prices']:
                from datetime import datetime, timezone
                app['reference_time'] = datetime.now(timezone.utc).isoformat()
                logger.info(f"Reference prices saved at {app['reference_time']} for {list(app['reference_prices'].keys())}")
            else:
                app['reference_time'] = None
                logger.info("No assets to track for reference pricing.")
    return ws
