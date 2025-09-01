import asyncio
from decimal import Decimal
from typing import Any, Dict, Optional, Set, List, TYPE_CHECKING
import logging

from ...protocols import Balances
from .exchange_utils import calculate_average_buy_price

if TYPE_CHECKING:
    from ...exchange_coordinator import ExchangeCoordinator


class BalanceManager:
    """잔고 관리를 전담하는 서비스 클래스"""

    def __init__(self, coordinator: "ExchangeCoordinator"):
        self.coordinator = coordinator
        self.exchange = coordinator.exchange
        self.logger = coordinator.logger
        self.name = coordinator.name
        self.quote_currency = coordinator.quote_currency
        self.app = coordinator.app
        self.follows = coordinator.follows
        self.testnet = coordinator.testnet
        self.whitelist = coordinator.whitelist

        # 캐시 데이터 초기화
        self.balances_cache: Dict[str, Dict[str, Any]] = {}

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

        # 모든 자산을 병렬로 처리 (testnet 시 whitelist로 제한)
        async with asyncio.TaskGroup() as tg:
            for asset, total_amount in total_balances.items():
                if not self.testnet or (asset in self.whitelist or asset == self.quote_currency):
                    tg.create_task(process_single_asset(asset, total_amount))

    def create_portfolio_update_message(self, symbol: str, balance_data: Dict[str, Any]) -> Dict[str, Any]:
        """포트폴리오 정보로부터 클라이언트에게 보낼 업데이트 메시지를 생성합니다."""
        free_amount = balance_data.get('free', Decimal('0'))
        locked_amount = balance_data.get('locked', Decimal('0'))
        avg_buy_price = balance_data.get('avg_buy_price')
        realised_pnl = balance_data.get('realised_pnl')

        # Get decimal places from config for rounding for display
        exchanges_config = self.app.get('config', {}).get('exchanges', {})
        decimal_places = exchanges_config.get(self.name, {}).get('value_decimal_places', 3)

        formatted_avg_buy_price = None
        if avg_buy_price is not None:
            # Round the Decimal before converting to float for sending.
            # The internal cache remains at full precision.
            formatted_avg_buy_price = float(round(avg_buy_price, decimal_places))

        formatted_realised_pnl = None
        if realised_pnl is not None:
            formatted_realised_pnl = float(round(realised_pnl, decimal_places))

        message = {
            'type': 'portfolio_update',
            'exchange': self.name,
            'symbol': symbol,
            'free': float(free_amount),
            'locked': float(locked_amount),
            'avg_buy_price': formatted_avg_buy_price,
            'realised_pnl': formatted_realised_pnl,
        }
        return message

    def handle_zero_balance(self, asset: str) -> None:
        """잔고가 0이 된 자산을 처리합니다."""
        if asset not in self.balances_cache:
            return

        # follow 목록에 있으면 잔고만 0으로 업데이트
        if asset in self.follows:
            self.logger.info(f"Asset {asset} balance is zero, but kept as it is followed.")
            self.balances_cache[asset]['free'] = Decimal('0')
            self.balances_cache[asset]['locked'] = Decimal('0')
            self.balances_cache[asset]['total_amount'] = Decimal('0')
            
            balance_data = self.balances_cache.get(asset, {})
            update_message = self.create_portfolio_update_message(asset, balance_data)
            asyncio.create_task(self.app['broadcast_message'](update_message))
        
        # follow 목록에 없으면 캐시에서 완전히 제거
        else:
            self.logger.info(f"Asset {asset} balance is zero and not followed. Removing from balance cache.")
            del self.balances_cache[asset]
            
            remove_message = {'type': 'remove_holding', 'symbol': f"{asset}/{self.quote_currency}", 'exchange': self.name}
            asyncio.create_task(self.app['broadcast_message'](remove_message))

        # 추적 자산 목록 업데이트 및 감시 루프 재시작 요청
        asyncio.create_task(self.coordinator.update_tracked_assets_and_restart_watcher())

    def add_balance(self, asset: str, total_amount: Decimal, free: Decimal, used: Decimal) -> None:
        """새로운 잔고 추가 또는 업데이트"""
        is_existing = asset in self.balances_cache
        is_positive = total_amount > Decimal('0')
        needs_update = False

        if is_positive:
            if not is_existing:
                self.logger.info(f"New asset detected in balance: {asset}, free: {free}, locked: {used}")
                self._init_dummy_balance(asset) # 새 자산에 대한 기본 항목 생성
                needs_update = True

            self.balances_cache[asset]['free'] = free
            self.balances_cache[asset]['locked'] = used
            self.balances_cache[asset]['total_amount'] = total_amount
        
        elif not is_positive and is_existing:
            self.handle_zero_balance(asset)

        if needs_update:
            asyncio.create_task(self.coordinator.update_tracked_assets_and_restart_watcher())

    def update_price(self, asset: str, price: Decimal) -> None:
        """자산 가격 업데이트"""
        if asset in self.balances_cache and price > 0:
            self.balances_cache[asset]['price'] = price

    async def update_average_price_on_buy(self, asset: str, filled_amount: Decimal, average_price: Decimal) -> None:
        """매수 체결 후 평균 매수 단가를 업데이트합니다."""
        if asset not in self.balances_cache:
            self.logger.warning(f"Cannot update avg_price for untracked asset: {asset}")
            return

        balances = self.balances_cache[asset]
        old_avg_price = balances.get('avg_buy_price')

        # User requirement: If avg_price is None, keep it None even on new buys.
        if old_avg_price is None:
            self.logger.info(f"Skipping avg_price update for {asset} because it is None.")
            return

        old_total_amount = balances.get('total_amount', Decimal('0'))

        # 새로운 평균 매수 단가 계산
        if old_avg_price <= 0 or old_total_amount <= 0:
            new_avg_price = average_price
        else:
            old_cost = old_total_amount * old_avg_price
            fill_cost = filled_amount * average_price
            new_total_amount = old_total_amount + filled_amount
            new_avg_price = (old_cost + fill_cost) / new_total_amount if new_total_amount > 0 else average_price

        balances['avg_buy_price'] = new_avg_price
        self.logger.info(f"Average price for {asset} updated to {new_avg_price} after buy.")

        # 업데이트된 잔고 정보 브로드캐스트
        update_message = self.create_portfolio_update_message(asset, balances)
        await self.app['broadcast_message'](update_message)

    async def update_realized_pnl_on_sell(self, asset: str, filled_amount: Decimal, average_price: Decimal) -> None:
        """매도 체결 후 실현 손익을 업데이트합니다."""
        if asset not in self.balances_cache:
            self.logger.warning(f"Cannot update pnl for untracked asset: {asset}")
            return

        balances = self.balances_cache[asset]
        avg_buy_price = balances.get('avg_buy_price')

        if avg_buy_price is None or avg_buy_price <= 0:
            self.logger.warning(f"Cannot calculate realized PnL for {asset} without avg_buy_price.")
            return

        # 실현 손익 계산
        profit = (average_price - avg_buy_price) * filled_amount
        
        # 기존 실현 손익에 누적
        if balances.get('realised_pnl') is None:
            balances['realised_pnl'] = profit
        else:
            balances['realised_pnl'] += profit

        self.logger.info(f"Realized PnL for {asset} updated by {profit}. Total: {balances['realised_pnl']}")

        # 업데이트된 잔고 정보 브로드캐스트
        update_message = self.create_portfolio_update_message(asset, balances)
        await self.app['broadcast_message'](update_message)