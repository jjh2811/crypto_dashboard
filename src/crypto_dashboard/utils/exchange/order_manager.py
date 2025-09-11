import asyncio
import json
from decimal import Decimal
from typing import Any, Dict, Set, TYPE_CHECKING, Optional

from ...models.trade_models import TradeCommand

if TYPE_CHECKING:
    from ...exchange_coordinator import ExchangeCoordinator


class OrderManager:
    """주문 관리를 전담하는 서비스 클래스"""

    def __init__(self, coordinator: "ExchangeCoordinator"):
        self.coordinator = coordinator
        self.exchange = coordinator.exchange
        self.logger = coordinator.logger
        self.name = coordinator.name
        self.quote_currency = coordinator.quote_currency
        self.app = coordinator.app
        self.balance_manager = coordinator.balance_manager

        # 캐시 데이터 초기화
        self.orders_cache: Dict[str, Dict[str, Any]] = {}
        
        # 설정 파일 로드
        with open('src/crypto_dashboard/config.json', 'r') as f:
            self.config = json.load(f)

    @staticmethod
    def _get_nested_value(data: Dict[str, Any], path: str) -> Optional[Any]:
        """점(.)으로 구분된 경로 문자열을 사용해 중첩된 딕셔너리에서 값을 가져옵니다."""
        keys = path.split('.')
        value = data
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return None
        return value

    def _is_order_triggered(self, order: Dict[str, Any]) -> bool:
        """설정 파일에 정의된 조건 목록에 따라 스탑 주문이 트리거되었는지 확인합니다."""
        if not order.get('stopPrice'):
            return False

        exchange_config = self.config.get('exchanges', {}).get(self.name, {})
        conditions = exchange_config.get('stop_trigger_conditions')

        # 조건이 리스트 형태가 아니면 처리하지 않음
        if not isinstance(conditions, list):
            return False

        # 조건 리스트를 순회하며 하나라도 맞으면 True 반환
        for condition in conditions:
            if isinstance(condition, dict) and 'path' in condition and 'expected_value' in condition:
                actual_value = self._get_nested_value(order, condition['path'])
                if actual_value is not None and actual_value == condition['expected_value']:
                    return True # 조건 중 하나라도 일치하면 즉시 True 반환
        
        # 모든 조건이 맞지 않으면 False 반환
        return False

    async def initialize_orders(self, open_orders: list) -> None:
        """초기 주문 상태 초기화"""
        for order in open_orders:
            price = Decimal(str(order.get('price') or 0))
            amount = Decimal(str(order.get('amount') or 0))
            filled = Decimal(str(order.get('filled') or 0))
            stop_price = order.get('stopPrice')
            order_id = order.get('id')
            if order_id:
                self.orders_cache[order_id] = {
                    'id': order_id,
                    'symbol': order.get('symbol', ''),
                    'side': order.get('side'),
                    'price': float(price),
                    'stop_price': float(stop_price) if stop_price is not None else None,
                    'amount': float(amount),
                    'filled': float(filled),
                    'value': float(price * (amount - filled)), # 미체결 수량 기준 가치
                    'timestamp': order.get('timestamp'),
                    'status': order.get('status'),
                    'is_triggered': self._is_order_triggered(order)
                }
        self.logger.info(f"Initialized {len(open_orders)} open orders.")

    async def cancel_order(self, order_id: str, symbol: str) -> None:
        """단일 주문 취소"""
        try:
            await self.app['broadcast_log']({'status': 'Cancelling', 'symbol': symbol, 'order_id': order_id}, self.name, self.logger)
            await self.exchange.cancel_order(order_id, symbol)
            self.logger.info(f"Successfully sent cancel request for order {order_id}")
            # 캐시 제거는 watch_orders 이벤트가 처리하도록 둠

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

        # 병렬로 모든 주문 취소 요청
        cancellation_tasks = []
        for order in all_orders:
            order_id = order.get('id')
            symbol = order.get('symbol', '')
            if order_id and symbol:
                task = asyncio.create_task(self.exchange.cancel_order(order_id, symbol))
                cancellation_tasks.append(task)

        results = await asyncio.gather(*cancellation_tasks, return_exceptions=True)

        for order, result in zip(all_orders, results):
            order_id = order.get('id')
            if isinstance(result, Exception):
                self.logger.error(f"Failed to cancel order {order_id}: {result}")
                await self.app['broadcast_log']({'status': 'Cancel Failed', 'symbol': order.get('symbol'), 'order_id': order_id, 'reason': str(result)}, self.name, self.logger)
            else:
                self.logger.info(f"Successfully sent cancel request for order {order_id}")

    def update_order(self, order: Dict[str, Any]) -> list:
        """주문 업데이트 처리 (웹소켓 이벤트에서 호출)"""
        tasks = []
        order_id = order.get('id')
        if not order_id:
            return []

        # 이전 주문 정보 가져오기
        old_order = self.orders_cache.get(order_id, {})
        old_filled = Decimal(str(old_order.get('filled', '0')))

        # 새 주문 정보 파싱
        status = order.get('status')
        new_filled = Decimal(str(order.get('filled', '0')))

        # 체결량 변화 감지
        trade_amount = new_filled - old_filled
        if trade_amount > 0:
            tasks.append(asyncio.create_task(self._handle_filled_order(order, trade_amount)))

        # 캐시 업데이트 및 브로드캐스트
        if status in ('closed', 'canceled'):
            if order_id in self.orders_cache:
                del self.orders_cache[order_id]
                self.logger.info(f"Order {order_id} ({status}) removed from cache.")
        else: # open (partially filled 포함)
            price = Decimal(str(order.get('price') or '0'))
            amount = Decimal(str(order.get('amount') or '0'))
            stop_price = order.get('stopPrice')
            self.orders_cache[order_id] = {
                'id': order_id,
                'symbol': order.get('symbol', ''),
                'side': order.get('side'),
                'price': float(price),
                'stop_price': float(stop_price) if stop_price is not None else None,
                'amount': float(amount),
                'filled': float(new_filled),
                'value': float(price * (amount - new_filled)),
                'timestamp': order.get('timestamp'),
                'status': status,
                'was_stop_order': bool(stop_price),  # 스탑 주문 여부 플래그
                'is_triggered': self._is_order_triggered(order)
            }

        # 프론트엔드에 주문 목록 업데이트 브로드캐스트
        tasks.append(asyncio.create_task(self.app['broadcast_orders_update'](self.app['exchanges'].get(self.name))))

        # 로그 브로드캐스트
        log_payload = {
            'status': status,
            'symbol': order.get('symbol'),
            'side': order.get('side'),
            'order_id': order_id,
            'price': float(order.get('average') or order.get('price') or '0'),
            'amount': float(trade_amount if trade_amount > 0 else order.get('amount', '0'))
        }

        # 스탑 주문 여부 확인 및 로그에 반영
        # 두 줄로 분할하여 None 안전하게 처리
        stop_price_in_order = order.get('stopPrice')
        trigger_price_in_order = order.get('triggerPrice')
        old_stop_price = old_order.get('stop_price')

        # 수치형 값에 대해 0보다 큰지만 확인 (0.0도 스탑 주문으로 취급하지 않음)
        current_stop_price = None
        if stop_price_in_order and isinstance(stop_price_in_order, (int, float)) and stop_price_in_order > 0:
            current_stop_price = stop_price_in_order
        elif trigger_price_in_order and isinstance(trigger_price_in_order, (int, float)) and trigger_price_in_order > 0:
            current_stop_price = trigger_price_in_order
        elif old_stop_price and isinstance(old_stop_price, (int, float)) and old_stop_price > 0:
            current_stop_price = old_stop_price

        was_stop_order = ((stop_price_in_order is not None and isinstance(stop_price_in_order, (int, float)) and stop_price_in_order > 0) or
                          (trigger_price_in_order is not None and isinstance(trigger_price_in_order, (int, float)) and trigger_price_in_order > 0) or
                          old_order.get('was_stop_order', False))

        # 1. 실제 주문 유형(limit/market)을 먼저 설정
        log_payload['order_type'] = order.get('type')

        # 2. 스탑 가격이 존재하고, 주문 상태가 'open'일 때만 payload에 추가
        if status == 'open' and (current_stop_price or was_stop_order):
            if current_stop_price:
                log_payload['stop_price'] = float(current_stop_price)
            log_payload['is_triggered'] = self._is_order_triggered(order)

        # 수수료 정보 추가
        if 'fee' in order and order['fee'] is not None:
            log_payload['fee'] = order['fee']
            
        tasks.append(asyncio.create_task(self.app['broadcast_log'](log_payload, self.name, self.logger)))

        # 주문 상태 변경이 추적 자산 목록에 영향을 줄 수 있으므로, 코디네이터에 업데이트 요청
        tasks.append(asyncio.create_task(self.coordinator.update_tracked_assets_and_restart_watcher()))

        return tasks


    async def _handle_filled_order(self, order: Dict[str, Any], trade_amount: Decimal):
        """체결된 주문을 처리하여 잔고 및 손익을 업데이트합니다."""
        side = order.get('side')
        symbol = order.get('symbol')
        asset = symbol.split('/')[0] if symbol else None

        # 체결 가격 (average가 있으면 사용, 없으면 price 사용)
        trade_price = Decimal(str(order.get('average') or order.get('price') or '0'))

        if not side or not asset or not trade_price > 0:
            self.logger.warning(f"Could not handle filled order due to missing data: {order}")
            return

        # 매수로 인해 새로운 자산을 보유하게 되었는지 확인
        is_new_holding = side == 'buy' and asset not in self.balance_manager.balances_cache
        if is_new_holding:
            self.logger.info(f"New asset '{asset}' acquired. Fetching initial price before updating balance.")
            try:
                # 가격 정보를 먼저 조회하고 브로드캐스트
                ticker = await self.exchange.fetch_ticker(symbol)
                price = ticker.get('last')
                percentage = ticker.get('percentage', 0.0)
                
                if price is not None:
                    price_update_message = {
                        'type': 'price_update',
                        'exchange': self.name,
                        'symbol': symbol,
                        'price': float(price),
                        'percentage': float(percentage)
                    }
                    await self.app['broadcast_message'](price_update_message)
                    self.logger.info(f"Broadcasted initial price for new holding '{asset}': {price}")
            except Exception as e:
                self.logger.warning(f"Could not fetch initial price for new holding {symbol}: {e}")

        self.logger.info(f"Handling filled order: {side} {trade_amount} {asset} at {trade_price}")

        if side == 'buy':
            await self.balance_manager.update_average_price_on_buy(asset, trade_amount, trade_price)
        elif side == 'sell':
            await self.balance_manager.update_realized_pnl_on_sell(asset, trade_amount, trade_price)

    def get_order_asset_names(self) -> Set[str]:
        """주문에서 자산 이름들을 추출"""
        return {o['symbol'].split('/')[0] for o in self.orders_cache.values() if o.get('symbol')}

    async def execute_trade_command(self, command: TradeCommand) -> Dict[str, Any]:
        """TradeCommand를 받아 주문 생성 및 실행 (TradeExecutor의 execute 리팩토링)"""
        if not command.symbol or not command.amount:
            self.logger.error("거래를 실행하려면 symbol과 amount가 반드시 필요합니다.")
            return {"status": "error", "message": "Symbol or amount is missing"}

        try:
            symbol = command.symbol
            asset = symbol.split('/')[0]

            # 새로운 코인인지 확인
            is_new_coin = asset not in self.coordinator.tracked_assets

            if is_new_coin:
                self.logger.info(f"New coin '{asset}' detected. Fetching price before creating order.")
                try:
                    # 가격 정보를 먼저 조회하고 브로드캐스트
                    ticker = await self.exchange.fetch_ticker(symbol)
                    price = ticker.get('last')
                    percentage = ticker.get('percentage', 0.0)
                    
                    if price is not None:
                        price_update_message = {
                            'type': 'price_update',
                            'exchange': self.name,
                            'symbol': symbol,
                            'price': float(price),
                            'percentage': float(percentage)
                        }
                        await self.app['broadcast_message'](price_update_message)
                        self.logger.info(f"Broadcasted initial price for new coin '{asset}': {price}")
                except Exception as e:
                    self.logger.warning(f"Could not fetch initial price for {symbol}: {e}. Proceeding with order creation.")

            # create_order에 필요한 파라미터들 준비
            order_type = command.order_type
            side = command.intent
            amount = float(command.amount)
            price = float(command.price) if command.price else None
            params = {}

            # Stop-price가 있는 경우 params에 추가
            if command.stop_price:
                params['stopPrice'] = float(command.stop_price)
                self.logger.info(f"Stop price found: {command.stop_price}")

            # 실제 주문 실행
            self.logger.info(f"Creating {order_type} order: {side} {amount} {symbol} at price {price} with params {params}")
            
            # WebSocket 주문을 지원하는지 확인하여 분기 처리
            if self.exchange.has.get('createOrderWs'):
                order = await self.exchange.create_order_ws(symbol, order_type, side, amount, price, params)
            else:
                order = await self.exchange.create_order(symbol, order_type, side, amount, price, params)

            self.logger.info("Successfully created order")

            # 주문 생성 후 watch_orders가 이벤트를 받아 처리하므로 별도 브로드캐스트 불필요.
            # watch_orders 핸들러가 update_tracked_assets_and_restart_watcher를 호출하여
            # 신규 코인이 tracked_assets에 추가되고 가격 감시가 시작됨.

            return {
                "status": "success",
                "order_details": order
            }

        except Exception as e:
            self.logger.error(f"Order creation failed: {e}", exc_info=True)
            return {"status": "error", "message": f"An unexpected error occurred: {e}"}