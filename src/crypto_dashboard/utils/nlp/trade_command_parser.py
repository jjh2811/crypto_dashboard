"""
거래 명령 파서 모듈
추출된 엔티티를 파싱하여 최종적이고 검증된 TradeCommand를 구성합니다.
상대 가격 계산, 총 비용을 수량으로 변환, 최종 주문 유형 결정과 같은 복잡한 로직을 처리합니다.
"""
from decimal import Decimal, InvalidOperation
import logging
from typing import Optional, Tuple

from ...models.trade_models import TradeCommand
from .entity_extractor import EntityExtractor


class TradeCommandParser:
    """
    추출된 엔티티를 파싱하여 최종적이고 검증된 `TradeCommand`를 구성합니다.
    상대 가격 계산, 총 비용을 수량으로 변환, 최종 주문 유형 결정과 같은 복잡한 로직을 처리합니다.
    """
    def __init__(self, extractor: EntityExtractor, exchange_base, logger: logging.Logger):  # type: ignore
        self.extractor = extractor
        self.exchange_base = exchange_base
        self.logger = logger

    def _adjust_precision(self, value: Optional[Decimal], symbol: str, value_type: str) -> Tuple[Optional[Decimal], Optional[str]]:
        """거래소 정밀도에 맞게 값을 조정하고, 실패 시 오류 메시지를 반환합니다."""
        if value is None:
            return None, None

        try:
            if value_type == 'price':
                adjusted_str = self.exchange_base.exchange.price_to_precision(symbol, float(value))
                return Decimal(adjusted_str), None
            elif value_type == 'amount':
                adjusted_str = self.exchange_base.exchange.amount_to_precision(symbol, float(value))
                return Decimal(adjusted_str), None
        except Exception as e:
            error_message = f"{value_type} 정밀도 조정 실패 (심볼: {symbol}, 값: {value}): {e}"
            self.logger.warning(error_message)
            return None, error_message

        return value, None

    async def parse(self, text: str) -> Optional[TradeCommand] | str:
        """주어진 텍스트를 파싱하여 TradeCommand 객체로 변환합니다."""
        entities = self.extractor.extract_entities(text)
        # 코인을 찾지 못한 경우 마켓 정보를 갱신하고 다시 시도
        should_refresh_markets = False
        if entities.get("intent") and entities.get("coin") is None:
            order_type = entities.get("order_type", "market")

            if order_type == "limit":  # 지정가 주문
                # 수량 조건: amount 또는 relative_amount 또는 total_cost
                amount_condition = (entities.get("amount") is not None or
                                  entities.get("relative_amount") is not None or
                                  entities.get("total_cost") is not None)
                # 가격 조건: price 또는 relative_price 또는 current_price_order
                price_condition = (entities.get("price") is not None or
                                  entities.get("relative_price") is not None or
                                  entities.get("current_price_order") is True)

                if amount_condition and price_condition:
                    should_refresh_markets = True

            elif order_type == "market":  # 시장가 주문
                # 수량 조건: amount 또는 relative_amount 또는 total_cost
                if (entities.get("amount") is not None or
                    entities.get("relative_amount") is not None or
                    entities.get("total_cost") is not None):
                    should_refresh_markets = True

        if should_refresh_markets:
            self.logger.info("코인을 찾지 못했지만 다른 주문 정보는 있습니다. 마켓 정보를 갱신하고 다시 시도합니다.")
            try:
                # 마켓 정보 갱신
                await self.exchange_base.exchange.load_markets(reload=True)

                # 코인 목록 업데이트
                unique_coins = {market['base'] for market in self.exchange_base.exchange.markets.values() if market.get('active') and market.get('base')}
                self.extractor.coins = sorted(list(unique_coins))

                # 최대 코인 길이 재계산
                self.extractor._update_max_coin_len()

                self.logger.info(f"마켓 정보 갱신 완료. 새로운 코인 목록 크기: {len(self.extractor.coins)}")

                # 엔티티 재추출
                entities = self.extractor.extract_entities(text)

            except Exception as e:
                self.logger.error(f"마켓 정보 갱신 실패: {e}")

        if not entities.get("intent") or not entities.get("coin"):
            error_message = f"Parse failed for text: '{text}'. Missing intent or coin."
            self.logger.warning(error_message)
            return error_message

        coin_symbol = str(entities["coin"])
        market_symbol = f"{coin_symbol}/{self.exchange_base.quote_currency}"

        # 변수 초기화
        final_price = entities.get("price")
        final_stop_price = entities.get("stop_price")
        final_amount = entities.get("amount")
        total_cost = entities.get("total_cost")

        # 상대 가격 및 상대 스탑 가격 처리를 위한 기준 가격 가져오기
        base_price_for_relative = None
        if entities.get("relative_price") is not None or entities.get("relative_stop_price") is not None:
            order_book = await self.exchange_base.price_manager.get_order_book(coin_symbol)
            if order_book:
                intent = str(entities["intent"])
                base_price_num = order_book['bid'] if intent == 'buy' else order_book['ask']
                base_price_for_relative = Decimal(str(base_price_num))
            else:
                error_message = f"'{coin_symbol}'의 호가를 가져올 수 없어 상대 가격 주문을 처리할 수 없습니다."
                self.logger.error(error_message)
                return error_message

        # 상대 가격 주문 처리
        if entities.get("relative_price") is not None and base_price_for_relative is not None:
            relative_price_percentage = entities["relative_price"]
            calculated_price = base_price_for_relative * (Decimal('1') + relative_price_percentage / Decimal('100'))
            final_price = calculated_price
            self.logger.info(
                f"상대 가격 주문: {coin_symbol} 기준가({base_price_for_relative}) 대비 {relative_price_percentage:+}% -> "
                f"지정가 {calculated_price}"
            )

        # 상대 스탑 가격 주문 처리
        if entities.get("relative_stop_price") is not None and base_price_for_relative is not None:
            relative_stop_price_percentage = entities["relative_stop_price"]
            calculated_stop_price = base_price_for_relative * (Decimal('1') + relative_stop_price_percentage / Decimal('100'))
            final_stop_price = calculated_stop_price
            self.logger.info(
                f"상대 스탑 가격 주문: {coin_symbol} 기준가({base_price_for_relative}) 대비 {relative_stop_price_percentage:+}% -> "
                f"스탑 가격 {calculated_stop_price}"
            )

        # 암시적 현재가 주문 처리
        elif entities.get("current_price_order") and final_price is None:
            order_book = await self.exchange_base.price_manager.get_order_book(coin_symbol)
            if order_book:
                price_to_set_num = order_book['bid'] if entities.get("intent") == 'buy' else order_book['ask']
                price_to_set = Decimal(str(price_to_set_num))
                final_price = price_to_set
                self.logger.info(f"암시적 현재가 설정: 지정가 {price_to_set}")
            else:
                error_message = f"'{coin_symbol}'의 호가를 가져올 수 없어 현재가 주문을 처리할 수 없습니다."
                self.logger.error(error_message)
                return error_message

        if final_amount is None and entities.get("relative_amount") is None and total_cost is None:
            error_message = f"Parse failed for text: '{text}'. Missing amount information."
            self.logger.warning(error_message)
            return error_message

        # 총 비용 기반 주문 처리
        if total_cost is not None:
            price_to_use = final_price
            if price_to_use is None:
                price_num = await self.exchange_base.price_manager.get_current_price(coin_symbol)
                if price_num:
                    price_to_use = Decimal(str(price_num))

            if price_to_use is not None and price_to_use > Decimal('0'):
                calculated_amount = total_cost / price_to_use
                final_amount = calculated_amount
                quote_currency = self.exchange_base.quote_currency
                self.logger.info(
                    f"계산된 수량: {total_cost} {quote_currency} / {price_to_use} {quote_currency}/coin -> "
                    f"{calculated_amount}"
                )
            else:
                error_message = f"'{coin_symbol}'의 현재 가격을 가져올 수 없어 총 비용 기반 주문을 처리할 수 없습니다."
                self.logger.error(error_message)
                return error_message

        # 상대 수량 주문 처리
        relative_amount_str = entities.get("relative_amount")
        if relative_amount_str:
            balance_info = self.exchange_base.balances_cache.get(coin_symbol, {})
            current_holding = balance_info.get('free', Decimal('0'))

            if current_holding <= Decimal('0'):
                error_message = f"상대 수량을 처리할 수 없습니다. '{coin_symbol}'의 보유량이 없습니다."
                self.logger.warning(error_message)
                return error_message

            try:
                percentage = Decimal(relative_amount_str)
                calculated_amount = current_holding * (percentage / Decimal('100'))
                final_amount = calculated_amount
                self.logger.info(
                    f"계산된 수량: {percentage}% of {current_holding} {coin_symbol} -> {calculated_amount}"
                )
            except InvalidOperation:
                error_message = f"잘못된 상대 수량 값입니다: '{relative_amount_str}'"
                self.logger.error(error_message)
                return error_message

        # 최종 정밀도 조정
        adjusted_amount, amount_error = self._adjust_precision(final_amount, market_symbol, 'amount')
        if amount_error: return amount_error

        adjusted_price, price_error = self._adjust_precision(final_price, market_symbol, 'price')
        if price_error: return price_error

        adjusted_stop_price, stop_price_error = self._adjust_precision(final_stop_price, market_symbol, 'price')
        if stop_price_error: return stop_price_error

        if adjusted_amount is not None and adjusted_amount <= Decimal('0'):
            error_message = f"계산된 거래 수량이 0 이하({adjusted_amount})이므로 거래를 진행할 수 없습니다."
            self.logger.warning(error_message)
            return error_message

        return TradeCommand(
            intent=str(entities["intent"]),
            symbol=market_symbol,
            amount=str(adjusted_amount) if adjusted_amount is not None else None,
            price=str(adjusted_price) if adjusted_price is not None else None,
            order_type=str(entities["order_type"]),
            stop_price=str(adjusted_stop_price) if adjusted_stop_price is not None else None,
            total_cost=str(total_cost) if total_cost is not None else None
        )