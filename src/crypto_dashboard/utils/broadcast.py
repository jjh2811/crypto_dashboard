"""
브로드캐스트 관련 모듈
프론트엔드 클라이언트들에게 메시지를 전송하는 기능을 제공합니다.
"""
import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, Union

# 전역 브로드캐스트 관련 변수들 (main 모듈에서 공유)
clients = set()
log_cache = []

# 전역 broadcast 함수들
broadcast_message = None
broadcast_orders_update = None
broadcast_log = None


def init_broadcast_functions(broadcast_msg_func, broadcast_orders_func, broadcast_log_func):
    """broadcast 함수들 초기화"""
    global broadcast_message, broadcast_orders_update, broadcast_log
    broadcast_message = broadcast_msg_func
    broadcast_orders_update = broadcast_orders_func
    broadcast_log = broadcast_log_func


async def basic_broadcast_message(message):
    """모든 연결된 클라이언트에게 메시지를 전송합니다."""
    for ws in list(clients):
        try:
            await ws.send_json(message)
        except ConnectionResetError:
            logging.warning(f"Failed to send message to a disconnected client.")


async def basic_broadcast_orders_update(exchange):
    """모든 클라이언트에게 현재 주문 목록을 전송합니다."""
    orders_with_exchange = []
    for order in exchange.order_manager.orders_cache.values():
        order_copy = order.copy()
        order_copy['exchange'] = exchange.name
        orders_with_exchange.append(order_copy)

    update_message = {'type': 'orders_update', 'data': orders_with_exchange}
    await basic_broadcast_message(update_message)


async def basic_broadcast_log(message, exchange_name=None, exchange_logger=None):
    """모든 클라이언트에게 로그 메시지를 전송합니다."""
    log_message = {
        'type': 'log',
        'message': message,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'exchange': exchange_name
    }
    log_cache.append(log_message)

    # Use exchange-specific logger if available, otherwise use root logger
    log_logger = exchange_logger if exchange_logger else logging.getLogger()
    log_logger.info(f"LOG: {message}")

    await basic_broadcast_message(log_message)


def get_clients():
    """클라이언트 목록 반환"""
    return clients


def get_log_cache():
    """로그 캐시 반환"""
    return log_cache