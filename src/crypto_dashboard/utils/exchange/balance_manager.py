import asyncio
from decimal import Decimal
from typing import Any, Dict, Optional, Set
import logging

from ...protocols import ExchangeProtocol, Balances
from .exchange_utils import calculate_average_buy_price


class BalanceManager:
    """잔고 관리를 전담하는 서비스 클래스"""

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
        self.follows: Set[str] = set()

        # 캐시 데이터 초기화
        self.balances_cache: Dict[str, Dict[str, Any]] = {}

        # 평균 가격 계산을 위한 추가 속성들
        self.tracked_assets: Set[str] = set()

    def add_follow_asset(self, asset: str) -> None:
        """감시할 자산 추가"""
        self.follows.add(asset)
        self._init_dummy_balance(asset)

    def _init_dummy_balance(self, asset: str) -> None:
        """follow 자산용 더미 잔고 생성"""
        if asset not in self.balances_cache:
            self.balances_cache[asset] = {
                'free': Decimal('0'),
                'locked': Decimal('0'),
                'total_amount': Decimal('0'),
                'price': Decimal('0'),
                'avg_buy_price': None,
                'realised_pnl': None
            }
            self.logger.info(f"[{self.name}] Added dummy balance for follow: {asset}")

    async def process_initial_balances(self, balance: Balances, total_balances: Dict[str, float]):
        """초기 잔고 처리 - 원래 코드의 _process_initial_balances"""
        async def process_single_asset(asset: str, total_amount: float) -> None:
            try:
                avg_buy_price, realised_pnl = await calculate_average_buy_price(
                    self.exchange,
                    asset,
                    Decimal(str(total_amount)),
                    self.quote_currency,
                    self.logger
                )

                free_amount = Decimal(str(balance.get('free', {}).get(asset, 0)))
                locked_amount = Decimal(str(balance.get('used', {}).get(asset, 0)))

                self.balances_cache[asset] = {
                    'free': free_amount,
                    'locked': locked_amount,
                    'total_amount': free_amount + locked_amount,
                    'price': Decimal('1.0') if asset == self.quote_currency else Decimal('0'),
                    'avg_buy_price': avg_buy_price,
                    'realised_pnl': realised_pnl
                }

                self.logger.info(f"Asset: {asset}, Avg Buy Price: {avg_buy_price if avg_buy_price is not None else 'N/A'}, Realised PnL: {realised_pnl}")

            except Exception as e:
                self.logger.error(f"Error calculating avg price for {asset}: {e}")

        # 모든 자산을 병렬로 처리
        async with asyncio.TaskGroup() as tg:
            for asset, total_amount in total_balances.items():
                tg.create_task(process_single_asset(asset, total_amount))

    def create_balance_update_message(self, symbol: str, balance_data: Dict[str, Any]) -> Dict[str, Any]:
        """잔고 정보로부터 클라이언트에게 보낼 업데이트 메시지를 생성합니다."""
        price = Decimal(str(balance_data.get('price', '0')))
        free_amount = balance_data.get('free', Decimal('0'))
        locked_amount = balance_data.get('locked', Decimal('0'))
        total_amount = free_amount + locked_amount
        value = price * total_amount
        avg_buy_price = balance_data.get('avg_buy_price')
        realised_pnl = balance_data.get('realised_pnl')

        unrealised_pnl = None
        if avg_buy_price is not None and price > 0:
            unrealised_pnl = (price - avg_buy_price) * total_amount

        message = {
            'type': 'balance_update',
            'exchange': self.name,
            'symbol': symbol,
            'price': float(price),
            'free': float(free_amount),
            'locked': float(locked_amount),
            'value': float(value),
            'avg_buy_price': float(avg_buy_price) if avg_buy_price is not None else None,
            'realised_pnl': float(realised_pnl) if realised_pnl is not None else None,
            'unrealised_pnl': float(unrealised_pnl) if unrealised_pnl is not None else None,
            'quote_currency': self.quote_currency
        }

        reference_prices = self.app.get('reference_prices', {})
        reference_time = self.app.get('reference_time')

        if reference_prices and self.name in reference_prices and symbol in reference_prices[self.name]:
            ref_price = Decimal(str(reference_prices[self.name][symbol]))
            if ref_price > 0:
                price_change_percent = (price - ref_price) / ref_price * 100
                message['price_change_percent'] = float(price_change_percent)
                message['reference_time'] = reference_time

        return message

    def handle_zero_balance(self, asset: str, is_existing: bool) -> None:
        """잔고가 0인 코인을 처리하는 공통 로직"""
        if not is_existing:
            return

        # 캐시 업데이트
        if asset in self.tracked_assets or asset in self.follows:
            # 잔고만 0으로 설정
            if asset in self.balances_cache:
                self.balances_cache[asset]['free'] = Decimal('0')
                self.balances_cache[asset]['locked'] = Decimal('0')
                self.balances_cache[asset]['total_amount'] = Decimal('0')
            self.logger.info(f"Asset zeroed but kept for tracking: {asset}")

            # 메시지 전송 (async)
            try:
                loop = asyncio.get_running_loop()
                balance_data = self.balances_cache.get(asset, {})
                update_message = self.create_balance_update_message(asset, balance_data)
                asyncio.create_task(self.app['broadcast_message'](update_message))
            except RuntimeError:
                # No event loop running
                pass

        else:
            # 완전 삭제
            if asset in self.balances_cache:
                del self.balances_cache[asset]
            self.logger.info(f"Asset completely removed: {asset}")

            # 제거 메시지 전송 (async)
            try:
                loop = asyncio.get_running_loop()
                remove_message = {'type': 'remove_holding', 'symbol': asset, 'exchange': self.name}
                asyncio.create_task(self.app['broadcast_message'](remove_message))
            except RuntimeError:
                # No event loop running
                pass

    def add_balance(self, asset: str, total_amount: Decimal, free: Decimal, used: Decimal) -> None:
        """새로운 잔고 추가 또는 업데이트"""
        is_existing = asset in self.balances_cache
        is_positive = total_amount > Decimal('0')

        if is_positive:
            if not is_existing:
                self.logger.info(f"New asset detected: {asset}, free: {free}, locked: {used}")

            self.balances_cache[asset]['free'] = free
            self.balances_cache[asset]['locked'] = used
            self.balances_cache[asset]['total_amount'] = total_amount
            self.balances_cache[asset]['price'] = Decimal('1.0') if asset == self.quote_currency else self.balances_cache[asset].get('price', Decimal('0'))
        elif not is_positive and is_existing:
            self.handle_zero_balance(asset, is_existing)

    def update_price(self, asset: str, price: Decimal) -> None:
        """자산 가격 업데이트"""
        if asset in self.balances_cache and price > 0:
            self.balances_cache[asset]['price'] = price