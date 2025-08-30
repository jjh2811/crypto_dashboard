import asyncio
from decimal import Decimal
from typing import Any, Dict, Optional, Set
import logging

from ...protocols import ExchangeProtocol


class OrderManager:
    """주문 관리를 전담하는 서비스 클래스"""

    def __init__(
        self,
        exchange: ExchangeProtocol,
        logger: logging.Logger,
        name: str,
        quote_currency: str,
        app: Any
    ):
        self.exchange = exchange
        self.logger = logger
        self.name = name
        self.quote_currency = quote_currency
        self.app = app

        # 캐시 데이터 초기화
        self.orders_cache: Dict[str, Dict[str, Any]] = {}

    async def initialize_orders(self, open_orders: list) -> None:
        """초기 주문 상태 초기화"""
        for order in open_orders:
            price = Decimal(str(order.get('price') or 0))
            amount = Decimal(str(order.get('amount') or 0))
            order_id = order.get('id')
            if order_id:
                self.orders_cache[order_id] = {
                    'id': order_id,
                    'symbol': order.get('symbol', ''),
                    'side': order.get('side'),
                    'price': float(price),
                    'amount': float(amount),
                    'value': float(price * amount),
                    'quote_currency': self.quote_currency,
                    'timestamp': order.get('timestamp'),
                    'status': order.get('status')
                }
        self.logger.info(f"Initialized {len(open_orders)} open orders.")

    async def cancel_order(self, order_id: str, symbol: str) -> None:
        """단일 주문 취소"""
        try:
            # 취소 진행 로그 전송
            await self.app['broadcast_log']({'status': 'Cancelling', 'symbol': symbol, 'order_id': order_id}, self.name, self.logger)

            # 실제 취소 요청
            cancelled_order = await self.exchange.cancel_order(order_id, symbol)
            self.logger.info(f"Successfully sent cancel request for order {order_id}")

            # 캐시에서 즉시 제거
            if order_id in self.orders_cache:
                del self.orders_cache[order_id]
                self.logger.info(f"Removed order {order_id} from cache after successful cancel request")

        except Exception as e:
            self.logger.error(f"Failed to cancel order {order_id}: {e}")
            await self.app['broadcast_log']({'status': 'Cancel Failed', 'symbol': symbol, 'order_id': order_id, 'reason': str(e)}, self.name, self.logger)

    async def cancel_all_orders(self) -> None:
        """모든 주문 취소"""
        self.logger.info("Received request to cancel all orders.")
        all_orders = list(self.orders_cache.values())
        if not all_orders:
            self.logger.info("No open orders to cancel.")
            await self.app['broadcast_log']({'status': 'Info', 'message': 'No open orders to cancel.'}, self.name, self.logger)
            return

        await self.app['broadcast_log']({'status': 'Info', 'message': f'Cancelling all {len(all_orders)} orders.'}, self.name, self.logger)

        for order in all_orders:
            order_id = order.get('id')
            symbol = order.get('symbol', '')
            if not order_id or not symbol:
                continue
            try:
                await self.exchange.cancel_order(order_id, symbol)
                self.logger.info(f"Successfully sent cancel request for order {order_id}")
            except Exception as e:
                self.logger.error(f"Failed to cancel order {order_id}: {e}")
                await self.app['broadcast_log']({'status': 'Cancel Failed', 'symbol': symbol, 'order_id': order_id, 'reason': str(e)}, self.name, self.logger)

        self.orders_cache.clear()
        await self.app['broadcast_orders_update'](self.app['exchanges'].get(self.name))

    def update_order(self, order: Dict[str, Any]) -> None:
        """주문 업데이트 처리 (웹소켓 이벤트에서 호출)"""
        order_id = order.get('id')
        symbol = order.get('symbol')
        status = order.get('status')
        if not all([order_id, symbol, status]):
            return

        asset = symbol.split('/')[0] if symbol else None
        if not asset:
            return

        side = order.get('side')
        price = Decimal(str(order.get('price') or '0'))
        amount = Decimal(str(order.get('amount') or '0'))
        filled = Decimal(str(order.get('filled') or '0'))

        log_payload = {'status': status, 'symbol': symbol, 'side': side, 'order_id': order_id}

        if status == 'open':
            if order_id and symbol:
                self.orders_cache[order_id] = {
                    'id': order_id,
                    'symbol': symbol,
                    'side': side,
                    'price': float(price),
                    'amount': float(amount),
                    'filled': float(filled),
                    'value': float(price * amount),
                    'quote_currency': self.quote_currency,
                    'timestamp': order.get('timestamp'),
                    'status': status
                }
                log_payload.update({'price': float(price), 'amount': float(amount)})
                asyncio.create_task(self.app['broadcast_log'](log_payload, self.name, self.logger))

                # 가격 fetch 및 업데이트
                asyncio.create_task(self._fetch_and_update_price(symbol, asset))

        elif status in ('closed', 'canceled'):
            if order_id in self.orders_cache:
                del self.orders_cache[order_id]

            if status == 'closed':
                log_payload.update({'price': float(order.get('average') or '0'), 'amount': float(filled)})

            asyncio.create_task(self.app['broadcast_log'](log_payload, self.name, self.logger))

        # 주문 업데이트 브로드캐스트
        asyncio.create_task(self.app['broadcast_orders_update'](self.app['exchanges'].get(self.name)))

    async def _fetch_and_update_price(self, symbol: str, asset: str) -> None:
        """가격 조회 및 업데이트"""
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            current_price = Decimal(str(ticker.get('last', '0')))
            if current_price > 0:
                update_message = {'symbol': asset, 'price': float(current_price)}
                await self.app['broadcast_message'](update_message)
                self.logger.debug(f"Fetched current price for {asset}: {current_price}")
        except Exception as e:
            self.logger.warning(f"Failed to fetch current price for {symbol}: {e}")

    def add_order_from_trade(self, asset: str, filled: Decimal, average_price: Decimal, side: str, balance_manager: Any) -> None:
        """거래 완료 시 잔고 평균 가격 업데이트"""
        if side == 'buy' and asset in balance_manager.balances_cache and filled > 0:
            balances = balance_manager.balances_cache[asset]
            old_total_amount = balances.get('total_amount', Decimal('0')) - filled
            old_avg_price = balances.get('avg_buy_price')

            if old_avg_price is None or old_avg_price <= 0 or old_total_amount <= 0:
                new_avg_price = average_price
            else:
                old_cost = old_total_amount * old_avg_price
                fill_cost = filled * average_price
                new_total_amount = old_total_amount + filled
                new_avg_price = (old_cost + fill_cost) / new_total_amount if new_total_amount > 0 else average_price

            balances['avg_buy_price'] = new_avg_price
            self.logger.info(f"Average price for {asset} updated to {new_avg_price} by trade.")

    def get_order_asset_names(self) -> Set[str]:
        """주문에서 자산 이름들을 추출"""
        return {o['symbol'].split('/')[0] for o in self.orders_cache.values() if o.get('symbol')}