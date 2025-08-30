import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional
import logging

from ...protocols import ExchangeProtocol


class PriceManager:
    """가격 관리를 전담하는 서비스 클래스"""

    def __init__(
        self,
        exchange: ExchangeProtocol,
        logger: logging.Logger,
        name: str,
        quote_currency: str,
        app: Any,
        balance_manager: Any
    ):
        self.exchange = exchange
        self.logger = logger
        self.name = name
        self.quote_currency = quote_currency
        self.app = app
        self.balance_manager = balance_manager

        # 가격 캐시들
        self.order_prices: Dict[str, Decimal] = {}
        self.ws_id_counter = 1

    async def initialize_prices_for_tracked_assets(self, tracked_assets: set) -> None:
        """추적중인 모든 자산들의 가격 초기화 (배치 조회)"""
        assets_to_fetch = [a for a in tracked_assets if a != self.quote_currency]
        if not assets_to_fetch:
            return

        symbols = [f"{asset}/{self.quote_currency}" for asset in assets_to_fetch]

        try:
            # 배치 조회 시도
            tickers = await self.exchange.fetch_tickers(symbols)
            for symbol, ticker in tickers.items():
                asset = symbol.split('/')[0]
                await self._update_asset_price(asset, Decimal(str(ticker.get('last', '0'))))
        except Exception:
            # 배치 조회 실패 시 개별 조회
            for asset in assets_to_fetch:
                try:
                    symbol = f"{asset}/{self.quote_currency}"
                    ticker = await self.exchange.fetch_ticker(symbol)
                    await self._update_asset_price(asset, Decimal(str(ticker.get('last', '0'))))
                except Exception as e:
                    self.logger.warning(f"Failed to fetch price for {asset}: {e}")

    async def _update_asset_price(self, asset: str, price: Decimal) -> None:
        """자산 가격 업데이트 및 브로드캐스트"""
        if asset in self.balance_manager.balances_cache:
            self.balance_manager.update_price(asset, price)
            update_message = self.balance_manager.create_balance_update_message(asset, self.balance_manager.balances_cache[asset])
            await self.app['broadcast_message'](update_message)
        else:
            # 잔고 캐시에 없으면 주문 가격으로 저장
            self.order_prices[asset] = price
            update_message = {'symbol': asset, 'price': float(price)}
            await self.app['broadcast_message'](update_message)

    async def fetch_and_update_price(self, symbol: str, asset: str) -> None:
        """특정 심볼 가격 조회 및 업데이트"""
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            current_price = Decimal(str(ticker.get('last', '0')))
            if current_price > 0:
                if asset in self.balance_manager.balances_cache:
                    self.balance_manager.balances_cache[asset]['price'] = current_price
                    update_message = self.balance_manager.create_balance_update_message(asset, self.balance_manager.balances_cache[asset])
                    await self.app['broadcast_message'](update_message)
                else:
                    # 잔고 캐시에 없으면 price_update 메시지 전송
                    update_message = {'symbol': asset, 'price': float(current_price)}
                    await self.app['broadcast_message'](update_message)
                    # 주문 가격도 업데이트
                    self.order_prices[asset] = current_price
                self.logger.debug(f"Fetched current price for {asset}: {current_price}")
        except Exception as e:
            self.logger.warning(f"Failed to fetch current price for {symbol}: {e}")

    def get_next_ws_id(self) -> int:
        """다음 웹소켓 ID 생성"""
        ws_id = self.ws_id_counter
        self.ws_id_counter += 1
        return ws_id

    async def watch_tickers_loop(self, tracked_assets: set) -> None:
        """가격 실시간 감시 루프"""
        while True:
            try:
                symbols = [f"{asset}/{self.quote_currency}" for asset in tracked_assets if asset != self.quote_currency]
                tickers = await self.exchange.watch_tickers(symbols)
                for symbol, ticker in tickers.items():
                    asset = symbol.split('/')[0]
                    price = ticker.get('last')

                    if not asset or not price:
                        continue

                    self.logger.debug(f"Price received for {asset}: {price}")

                    if asset in self.balance_manager.balances_cache:
                        self.balance_manager.balances_cache[asset]['price'] = Decimal(str(price))
                        update_message = self.balance_manager.create_balance_update_message(asset, self.balance_manager.balances_cache[asset])
                    else:
                        # 잔고 캐시에 없는 경우 price_update 메시지 전송
                        update_message = {
                            'type': 'price_update',
                            'exchange': self.name,
                            'symbol': asset,
                            'price': float(price)
                        }

                    await self.app['broadcast_message'](update_message)

            except Exception as e:
                self.logger.error(f"An error occurred in price watch loop for {self.name}: {e}", exc_info=True)
                await asyncio.sleep(5)