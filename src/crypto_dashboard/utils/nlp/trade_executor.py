"""
거래 실행기 모듈
TradeCommand를 거래소에 대해 실행합니다.
CCXT 라이브러리와 상호 작용하여 실제 주문을 생성하고,
가격을 조회하며, 거래소와의 연결을 관리하는 역할을 담당합니다.
"""
import logging
from typing import Dict, Optional

from ccxt.base.types import Num, Order

from ...protocols import ExchangeProtocol
from ...models.trade_models import TradeCommand


class TradeExecutor:
    """
    `TradeCommand`를 거래소에 대해 실행합니다.
    이 클래스는 `ccxt` 라이브러리와 상호 작용하여 실제 주문을 생성하고,
    가격을 조회하며, 거래소와의 연결을 관리하는 역할을 담당합니다.
    """

    def __init__(self, exchange: ExchangeProtocol, quote_currency: str, logger: logging.Logger):
        self.exchange = exchange
        self.quote_currency = quote_currency
        self.logger = logger

    async def get_current_price(self, coin_symbol: str) -> Optional[Num]:
        """지정된 코인의 현재 가격을 가져옵니다."""
        market_symbol = f'{coin_symbol}/{self.quote_currency}'
        try:
            ticker = await self.exchange.fetch_ticker(market_symbol)
            return ticker['last']
        except Exception as e:
            self.logger.error(f"Could not fetch price for {market_symbol}: {e}")
            return None

    async def get_order_book(self, coin_symbol: str) -> Optional[Dict[str, Num]]:
        """지정된 코인의 오더북을 가져와 1호가(매수/매도)를 반환합니다."""
        market_symbol = f'{coin_symbol}/{self.quote_currency}'
        try:
            order_book = await self.exchange.fetch_order_book(market_symbol, limit=1)
            if order_book['bids'] and order_book['asks']:
                best_bid = order_book['bids'][0][0]
                best_ask = order_book['asks'][0][0]
                self.logger.info(f"Order book for {market_symbol}: Best Bid={best_bid}, Best Ask={best_ask}")
                return {'bid': best_bid, 'ask': best_ask}
            else:
                self.logger.warning(f"Order book for {market_symbol} is empty.")
                return None
        except Exception as e:
            self.logger.error(f"Could not fetch order book for {market_symbol}: {e}")
            return None

    async def execute(self, command: TradeCommand) -> Dict:
        """주어진 명령을 실행하고 결과를 JSON 호환 딕셔너리로 반환합니다."""
        from ...models.trade_models import TradeCommand  # Import here to avoid circular import
        self.logger.info(f"Executing command: {command}")

        if not command.symbol or not command.amount:
            self.logger.error("거래를 실행하려면 symbol과 amount가 반드시 필요합니다.")
            return {"status": "error", "message": "Symbol or amount is missing"}

        try:
            # create_order 메서드에 필요한 파라미터들을 준비합니다.
            symbol = command.symbol
            order_type = command.order_type
            side = command.intent
            amount = float(command.amount)
            price = float(command.price) if command.price else None

            # 실제 주문을 실행합니다.
            self.logger.info(f"Placing order: {side} {amount} {symbol} at price {price}")
            order: Order = await self.exchange.create_order(symbol, order_type, side, amount, price)
            self.logger.info(f"Successfully placed order")

            return {
                "status": "success",
                "order_details": order
            }

        except Exception as e:
            return {"status": "error", "message": f"An unexpected error occurred: {e}"}