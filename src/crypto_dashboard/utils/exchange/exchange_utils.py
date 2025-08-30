import logging
from decimal import Decimal
from typing import Optional, Tuple

from ccxt.base.types import Order
from ...protocols import ExchangeProtocol


async def calculate_average_buy_price(
    exchange: ExchangeProtocol,
    asset: str,
    current_amount: Decimal,
    quote_currency: str,
    logger: logging.Logger
) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    if asset == quote_currency or current_amount <= 0:
        return None, None

    symbol = f"{asset}/{quote_currency}"
    try:
        trade_history = await exchange.fetch_closed_orders(symbol=symbol)
        if not trade_history:
            return None, None

        def get_timestamp(trade: Order) -> int:
            """Helper to get a timestamp from a trade, defaulting to 0 if missing or invalid."""
            ts = trade.get('timestamp', 0)
            return int(ts) if isinstance(ts, (int, float)) else 0

        sorted_trades = sorted(trade_history, key=get_timestamp)

        running_amount = current_amount
        start_index = -1

        for i in range(len(sorted_trades) - 1, -1, -1):
            trade = sorted_trades[i]
            side = trade.get('side')
            filled = Decimal(str(trade.get('filled', '0')))

            if side == 'sell':
                running_amount += filled
            elif side == 'buy':
                running_amount -= filled

            if running_amount == Decimal('0'):
                start_index = i
                break

        if start_index == -1:
            return None, None

        total_cost = Decimal('0')
        total_amount_bought = Decimal('0')
        realised_pnl = Decimal('0')

        for i in range(start_index, len(sorted_trades)):
            trade = sorted_trades[i]
            side = trade.get('side')
            filled = Decimal(str(trade.get('filled', '0')))
            price = Decimal(str(trade.get('price', '0')))

            if side == 'buy':
                total_cost += filled * price
                total_amount_bought += filled
            elif side == 'sell':
                avg_buy_price = total_cost / total_amount_bought if total_amount_bought > 0 else Decimal('0')
                if avg_buy_price > 0:
                    realised_pnl += (price - avg_buy_price) * filled
                    total_cost -= avg_buy_price * filled
                    total_amount_bought -= filled


        if total_amount_bought > Decimal('0'):
            avg_buy_price = total_cost / total_amount_bought
            return avg_buy_price, realised_pnl

        return None, realised_pnl

    except Exception as e:
        logger.error(f"Error calculating average buy price for {asset}: {e}", exc_info=True)
        return None, None