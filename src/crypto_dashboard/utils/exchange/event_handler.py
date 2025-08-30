import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set
import logging

from ...protocols import Balances


class EventHandler:
    """웹소켓 이벤트 감시와 처리를 전담하는 서비스 클래스"""

    def __init__(
        self,
        exchange_name: str,
        exchange_api,
        balance_manager: Any,
        order_manager: Any,
        price_manager: Any,
        app: Any,
        logger: logging.Logger,
        quote_currency: str,
        testnet: bool = False,
        whitelist: Optional[List[str]] = None
    ):
        self.name = exchange_name
        self.exchange = exchange_api
        self.balance_manager = balance_manager
        self.order_manager = order_manager
        self.price_manager = price_manager
        self.app = app
        self.logger = logger
        self.quote_currency = quote_currency
        self.testnet = testnet
        self.whitelist = whitelist or []

    async def watch_balance_loop(self) -> None:
        """잔고 업데이트 감시 루프"""
        while True:
            try:
                balance = await self.exchange.watch_balance()
                total_balances = balance.get('total', {})
                free_balances = balance.get('free', {})
                used_balances = balance.get('used', {})

                if not isinstance(total_balances, dict):
                    self.logger.warning("Total balances not a dict, skipping update")
                    continue

                for asset, total in total_balances.items():
                    if self.testnet and asset not in self.whitelist:
                        continue

                    free = free_balances.get(asset, '0')
                    used = used_balances.get(asset, '0')

                    free_amount = Decimal(str(free))
                    locked_amount = Decimal(str(used))
                    total_amount = free_amount + locked_amount

                    balance_data = {
                        'asset': asset,
                        'total': total_amount,
                        'free': free_amount,
                        'used': locked_amount
                    }

                    # 잔고 업데이트를 balance_manager에 위임
                    self.balance_manager.add_balance(asset, total_amount, free_amount, locked_amount)

                    # 업데이트 메시지를 broadcast
                    is_existing = asset in self.balance_manager.balances_cache
                    is_positive = total_amount > Decimal('0')

                    if is_positive:
                        if not is_existing:
                            self.logger.info(f"New asset detected: {asset}, free: {free_amount}, locked: {locked_amount}")

                        update_message = self.balance_manager.create_balance_update_message(asset, self.balance_manager.balances_cache[asset])
                        await self.app['broadcast_message'](update_message)

                    elif not is_positive and is_existing:
                        self.balance_manager.handle_zero_balance(asset, is_existing)

            except Exception as e:
                self.logger.error(f"Error in {self.name} balance watch loop: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def watch_orders_loop(self) -> None:
        """주문 업데이트 감시 루프"""
        while True:
            try:
                orders = await self.exchange.watch_orders()
                for order in orders:
                    self.order_manager.update_order(order)

            except Exception as e:
                self.logger.error(f"Error in {self.name} orders watch loop: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def watch_tickers_loop(self) -> None:
        """가격 업데이트 감시 루프"""
        tracked_assets = set()
        while True:
            try:
                # price_manager에게 최신 tracked_assets 요청
                all_tracked = (
                    set(self.balance_manager.balances_cache.keys()) |
                    set(self.order_manager.get_order_asset_names()) |
                    self.balance_manager.follows
                )

                # testnet 시 whitelist로 제한
                if self.testnet:
                    allowed_assets = set(self.whitelist)
                    all_tracked &= allowed_assets

                # 업데이트된 자산 목록이 있는 경우 재연결
                if all_tracked != tracked_assets:
                    tracked_assets = all_tracked.copy()
                    # 여기서는 간단히 제외하고, 메인 loop에서 update_price를 호출
                    # 실제로는 price_manager의 watch_tickers_loop를 사용해야 함
                    continue

                # price_manager의 감시 루프 실행
                await self.price_manager.watch_tickers_loop(tracked_assets)

            except Exception as e:
                self.logger.error(f"Error in {self.name} tickers watch loop: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def start_monitoring(self) -> None:
        """모든 이벤트 감시 루프 시작"""
        self.logger.info(f"Starting event monitoring loops for {self.name}")

        # 각 감시 루프를 백그라운드 태스크로 시작
        balance_task = asyncio.create_task(self.watch_balance_loop())
        orders_task = asyncio.create_task(self.watch_orders_loop())
        tickers_task = asyncio.create_task(self.watch_tickers_loop())

        await asyncio.gather(balance_task, orders_task, tickers_task)

    async def stop_monitoring(self) -> None:
        """모든 이벤트 감시 루프 중지"""
        self.logger.info(f"Stopping event monitoring loops for {self.name}")
        # 실제 구현에서 각 태스크들을 취소할 수 있음