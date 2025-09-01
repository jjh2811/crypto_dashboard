import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...exchange_coordinator import ExchangeCoordinator


class EventHandler:
    """웹소켓 이벤트 감시와 처리를 전담하는 서비스 클래스"""

    def __init__(self, coordinator: "ExchangeCoordinator"):
        self.coordinator = coordinator
        self.exchange = coordinator.exchange
        self.logger = coordinator.logger
        self.name = coordinator.name
        self.testnet = coordinator.testnet
        self.whitelist = coordinator.whitelist
        self.balance_manager = coordinator.balance_manager
        self.order_manager = coordinator.order_manager

    async def watch_balance_loop(self) -> None:
        """잔고 업데이트 감시 루프"""
        while True:
            try:
                balance_update = await self.exchange.watch_balance()

                # watch_balance가 전체 잔고를 반환하는 경우
                if 'total' in balance_update:
                    all_assets = (
                        set(balance_update.get('total', {}).keys()) |
                        set(balance_update.get('free', {}).keys()) |
                        set(balance_update.get('used', {}).keys())
                    )

                    for asset in all_assets:
                        if self.testnet and asset not in self.whitelist and asset != self.coordinator.quote_currency:
                            continue

                        total = Decimal(str(balance_update.get('total', {}).get(asset, '0')))
                        free = Decimal(str(balance_update.get('free', {}).get(asset, '0')))
                        used = Decimal(str(balance_update.get('used', {}).get(asset, '0')))

                        # total이 free + used와 다를 경우, total을 우선
                        if total != free + used:
                            # ccxt는 종종 total만 제공하거나 free/used만 제공
                            if free and used:
                                total = free + used

                        self.balance_manager.add_balance(asset, total, free, used)

                        # 변경된 잔고 즉시 브로드캐스트
                        updated_balance = self.balance_manager.balances_cache.get(asset)
                        if updated_balance:
                            message = self.balance_manager.create_balance_update_message(asset, updated_balance)
                            asyncio.create_task(self.coordinator.app['broadcast_message'](message))

                # watch_balance가 단일 자산 변경을 반환하는 경우 (e.g. binance)
                elif 'asset' in balance_update:
                    asset = balance_update['asset']
                    if self.testnet and asset not in self.whitelist and asset != self.coordinator.quote_currency:
                        continue

                    free = Decimal(str(balance_update.get('free', '0')))
                    used = Decimal(str(balance_update.get('used', '0')))
                    total = free + used # 단일 업데이트는 free, used로 total 계산

                    self.balance_manager.add_balance(asset, total, free, used)

                    # 변경된 잔고 즉시 브로드캐스트
                    updated_balance = self.balance_manager.balances_cache.get(asset)
                    if updated_balance:
                        message = self.balance_manager.create_balance_update_message(asset, updated_balance)
                        asyncio.create_task(self.coordinator.app['broadcast_message'](message))

            except asyncio.CancelledError:
                self.logger.info(f"Balance watch loop for {self.name} cancelled.")
                break
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

            except asyncio.CancelledError:
                self.logger.info(f"Order watch loop for {self.name} cancelled.")
                break
            except Exception as e:
                self.logger.error(f"Error in {self.name} orders watch loop: {e}", exc_info=True)
                await asyncio.sleep(5)
