import asyncio
from decimal import Decimal
from typing import Dict, List, Optional, TYPE_CHECKING

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
                    await self._update_asset_price(asset, symbol, Decimal(str(price)))
        except Exception as e:
            self.logger.warning(f"Batch fetch_tickers failed: {e}. Falling back to individual fetches.")
            for asset in assets_to_fetch:
                try:
                    symbol = f"{asset}/{self.quote_currency}"
                    ticker = await self.exchange.fetch_ticker(symbol)
                    price = ticker.get('last')
                    if price is not None:
                        await self._update_asset_price(asset, symbol, Decimal(str(price)))
                except Exception as e_single:
                    self.logger.warning(f"Failed to fetch price for {asset}: {e_single}")

    async def _update_asset_price(self, asset: str, symbol: str, price: Decimal) -> None:
        """자산 가격 업데이트 및 브로드캐스트"""
        if price <= 0:
            return

        # 1. 모든 추적 자산에 대해 price_update 메시지를 항상 전송합니다.
        update_message = {
            'type': 'price_update',
            'exchange': self.name,
            'symbol': symbol,
            'price': float(price)
        }
        await self.app['broadcast_message'](update_message)

        # 2. 만약 보유 자산이라면, 백엔드 내부 캐시에도 가격을 업데이트합니다.
        if asset in self.balance_manager.balances_cache:
            self.balance_manager.update_price(asset, price)

    async def watch_tickers_loop(self, symbols: List[str]) -> None:
        """가격 실시간 감시 루프"""
        self.logger.info(f"Starting ticker watch for: {symbols}")
        while True:
            try:
                tickers = await self.exchange.watch_tickers(symbols)
                for symbol, ticker in tickers.items():
                    price = ticker.get('last')
                    if price is None:
                        continue
                    
                    asset = symbol.split('/')[0]
                    if not asset:
                        continue

                    await self._update_asset_price(asset, symbol, Decimal(str(price)))

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

    async def get_current_price(self, coin_symbol: str) -> Optional[Decimal]:
        """코드 최적화: 캐시 우선 조회, 실패 시 실시간 fetch"""
        # 1. 잔고 캐시에서 가격 우선 확인 (최적화)
        if coin_symbol in self.balance_manager.balances_cache:
            balance_info = self.balance_manager.balances_cache[coin_symbol]
            cached_price = balance_info.get('price')
            if cached_price and cached_price > 0:
                self.logger.debug(f"Using cached price for {coin_symbol}: {cached_price}")
                return cached_price

        # 2. 캐시 미스 시 실시간 조회 (fallback)
        market_symbol = f'{coin_symbol}/{self.quote_currency}'
        try:
            ticker = await self.exchange.fetch_ticker(market_symbol)
            price = ticker.get('last')
            if price is not None:
                price_decimal = Decimal(str(price))
                # 조회한 가격을 캐시에 업데이트하여 향후 활용
                await self._update_asset_price(coin_symbol, market_symbol, price_decimal)
                return price_decimal
            return None
        except Exception as e:
            self.logger.error(f"Could not fetch price for {market_symbol}: {e}")
            return None

    async def get_order_book(self, coin_symbol: str) -> Optional[Dict[str, Decimal]]:
        """오더북 조회 (1호가)"""
        market_symbol = f'{coin_symbol}/{self.quote_currency}'
        try:
            order_book = await self.exchange.fetch_order_book(market_symbol, limit=1)
            if order_book['bids'] and order_book['asks']:
                return {
                    'bid': Decimal(str(order_book['bids'][0][0])),
                    'ask': Decimal(str(order_book['asks'][0][0]))
                }
            return None
        except Exception as e:
            self.logger.error(f"Could not fetch order book for {market_symbol}: {e}")
            return None
