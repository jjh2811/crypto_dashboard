import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from ...exchange_coordinator import ExchangeCoordinator


class PriceManager:
    """가격 관리를 전담하는 서비스 클래스"""

    def __init__(self, coordinator: "ExchangeCoordinator"):
        self.coordinator = coordinator
        self.exchange = coordinator.exchange
        self.logger = coordinator.logger
        self.name = coordinator.name
        self.quote_currency = coordinator.quote_currency
        self.app = coordinator.app
        self.balance_manager = coordinator.balance_manager

    async def initialize_prices_for_tracked_assets(self, tracked_assets: set) -> None:
        """추적중인 모든 자산들의 가격 초기화 (배치 조회)"""
        assets_to_fetch = [a for a in tracked_assets if a != self.quote_currency]
        if not assets_to_fetch:
            return

        symbols = [f"{asset}/{self.quote_currency}" for asset in assets_to_fetch]
        self.logger.info(f"Fetching initial prices for: {symbols}")

        try:
            tickers = await self.exchange.fetch_tickers(symbols)
            for symbol, ticker in tickers.items():
                asset = symbol.split('/')[0]
                price = ticker.get('last')
                if price is not None:
                    await self._update_asset_price(asset, Decimal(str(price)))
        except Exception as e:
            self.logger.warning(f"Batch fetch_tickers failed: {e}. Falling back to individual fetches.")
            for asset in assets_to_fetch:
                try:
                    symbol = f"{asset}/{self.quote_currency}"
                    ticker = await self.exchange.fetch_ticker(symbol)
                    price = ticker.get('last')
                    if price is not None:
                        await self._update_asset_price(asset, Decimal(str(price)))
                except Exception as e_single:
                    self.logger.warning(f"Failed to fetch price for {asset}: {e_single}")

    async def _update_asset_price(self, asset: str, price: Decimal) -> None:
        """자산 가격 업데이트 및 브로드캐스트"""
        if price <= 0:
            return

        # 보유/관심 자산인 경우, 전체 잔고 메시지 전송
        if asset in self.balance_manager.balances_cache:
            self.balance_manager.update_price(asset, price)
            balance_data = self.balance_manager.balances_cache[asset]
            update_message = self.balance_manager.create_balance_update_message(asset, balance_data)
            await self.app['broadcast_message'](update_message)
        
        # 보유/관심 자산이 아닌 경우 (예: 미체결 주문), 가격 정보만 전송
        else:
            self.logger.debug(f"Sending price-only update for non-portfolio asset {asset}.")
            update_message = {
                'type': 'price_update',
                'exchange': self.name,
                'symbol': asset,
                'price': float(price)
            }
            await self.app['broadcast_message'](update_message)

    async def watch_tickers_loop(self, symbols: List[str]) -> None:
        """가격 실시간 감시 루프"""
        self.logger.info(f"Starting ticker watch for: {symbols}")
        while True:
            try:
                tickers = await self.exchange.watch_tickers(symbols)
                for symbol, ticker in tickers.items():
                    asset = symbol.split('/')[0]
                    price = ticker.get('last')

                    if not asset or price is None:
                        continue

                    await self._update_asset_price(asset, Decimal(str(price)))

            except asyncio.CancelledError:
                self.logger.info("Ticker watch loop cancelled.")
                break # 루프 정상 종료
            except Exception as e:
                self.logger.error(f"An error occurred in price watch loop for {self.name}: {e}", exc_info=True)
                await asyncio.sleep(5) # 에러 발생 시 잠시 대기 후 재시도

    def get_tracked_symbols(self) -> List[str]:
        """현재 추적중인 모든 심볼 목록 반환"""
        return [
            f"{asset}/{self.quote_currency}" 
            for asset in self.coordinator.tracked_assets 
            if asset != self.quote_currency
        ]