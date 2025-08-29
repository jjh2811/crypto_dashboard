import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Set, cast

from aiohttp import web
import ccxt.pro as ccxtpro

from .exchange_base import ExchangeBase
from .exchange_utils import calculate_average_buy_price
from .protocols import Balances, ExchangeProtocol


class BinanceExchange(ExchangeBase):
    def __init__(self, api_key: str, secret_key: str, app: web.Application, exchange_name: str) -> None:
        super().__init__(api_key, secret_key, app, exchange_name)

    def _create_exchange_instance(self, api_key: str, secret_key: str, exchange_config: Dict[str, Any]) -> ExchangeProtocol:
        self.testnet = exchange_config['testnet']['use']
        self.whitelist = exchange_config['testnet']['whitelist'] if self.testnet else []

        exchange = ccxtpro.binance({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
                'warnOnFetchOpenOrdersWithoutSymbol': False,
            },
        })

        if self.testnet:
            exchange.set_sandbox_mode(True)

        return cast(ExchangeProtocol, exchange)

    async def _process_initial_balances(self, balance: Balances, total_balances: Dict[str, float]):
        if self.testnet:
            original_assets = list(total_balances.keys())
            total_balances = {
                asset: total for asset, total in total_balances.items() if asset in self.whitelist
            }
            self.logger.info(f"Testnet mode: Filtering balances with whitelist {self.whitelist}. Kept: {list(total_balances.keys())} from {original_assets}")

        await super()._process_initial_balances(balance, total_balances)

    async def watch_tickers_loop(self) -> None:
        while True:
            try:
                symbols = [f"{asset}/{self.quote_currency}" for asset in self.tracked_assets if asset != self.quote_currency]
                tickers = await self.exchange.watch_tickers(symbols)
                for symbol, ticker in tickers.items():
                    asset = symbol.split('/')[0]
                    price = ticker.get('last')
                    if not asset or not price:
                        continue
                    self.logger.debug(f"Price received: {asset} = {price}")

                    if asset in self.balances_cache:
                        self.balances_cache[asset]['price'] = Decimal(price)
                        update_message = self.create_balance_update_message(asset, self.balances_cache[asset])
                    else:
                        update_message = {'symbol': asset, 'price': float(Decimal(price))}

                    await self.app['broadcast_message'](update_message)
            except Exception as e:
                self.logger.error(f"An error occurred in watch_tickers_loop: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def watch_balance_loop(self) -> None:
        while True:
            try:
                balance = await self.exchange.watch_balance()
                for asset, bal in balance.items():
                    if self.testnet and asset not in self.whitelist:
                        continue

                    free_amount = Decimal(bal.get('free') or '0')
                    locked_amount = Decimal(bal.get('used') or '0')
                    total_amount = free_amount + locked_amount

                    is_existing = asset in self.balances_cache
                    is_positive_total = total_amount > Decimal('0')

                    if is_positive_total:
                        old_free = self.balances_cache.get(asset, {}).get('free', Decimal('0'))
                        old_locked = self.balances_cache.get(asset, {}).get('locked', Decimal('0'))
                        has_changed = (free_amount != old_free) or (locked_amount != old_locked)

                        if not is_existing:
                            self.logger.info(f"New asset detected: {asset}, free: {free_amount}, locked: {locked_amount}")
                            self.balances_cache[asset] = {'price': 0}

                        self.balances_cache[asset]['free'] = free_amount
                        self.balances_cache[asset]['locked'] = locked_amount
                        self.balances_cache[asset]['total_amount'] = total_amount

                        if has_changed:
                            self.logger.info(f"Balance for {asset} updated. Free: {free_amount}, Locked: {locked_amount}")
                            if 'avg_buy_price' not in self.balances_cache[asset]:
                                 avg_buy_price, realised_pnl = await calculate_average_buy_price(self.exchange, asset, total_amount, self.quote_currency, self.logger)
                                 self.balances_cache[asset]['avg_buy_price'] = avg_buy_price
                                 self.balances_cache[asset]['realised_pnl'] = realised_pnl

                            update_message = self.create_balance_update_message(asset, self.balances_cache[asset])
                            await self.app['broadcast_message'](update_message)

                        if asset == self.quote_currency and self.balances_cache[asset].get('price', 0) == 0:
                            self.balances_cache[asset]['price'] = 1.0

                    elif not is_positive_total and is_existing:
                        self._handle_zero_balance(asset, is_existing)

            except Exception as e:
                self.logger.error(f"Error in watch_balance_loop: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def watch_orders_loop(self) -> None:
        while True:
            try:
                orders = await self.exchange.watch_orders()
                for order in orders:
                    order_id = order.get('id')
                    symbol = order.get('symbol')
                    status = order.get('status')
                    if not all([order_id, symbol, status]):
                        continue

                    price = Decimal(order.get('price') or '0')
                    original_amount = Decimal(order.get('amount') or '0')
                    filled = Decimal(order.get('filled') or '0')
                    side = order.get('side')
                    if not all([order_id, symbol, status]):
                        continue

                    asset = symbol.replace(self.quote_currency, '') if symbol else None
                    if not asset:
                        continue


                    log_payload: Dict[str, Any] = {
                        'status': status,
                        'symbol': symbol,
                        'side': side,
                        'order_id': order_id,
                    }

                    if status == 'open':
                        assert order_id is not None
                        self.orders_cache[order_id] = {
                            'id': order_id,
                            'symbol': symbol,
                            'side': side,
                            'price': float(price),
                            'amount': float(original_amount),
                            'filled': float(filled),
                            'value': float(price * original_amount),
                            'quote_currency': self.quote_currency,
                            'timestamp': order.get('timestamp'),
                            'status': status
                        }
                        self.logger.info(f"New order: {order_id} - {symbol} {status}")
                        log_payload.update({'price': float(price), 'amount': float(original_amount)})
                        await self.app['broadcast_log'](log_payload, self.name, self.logger)

                        assert symbol is not None
                        await self._fetch_and_update_price(symbol, asset)

                    elif status == 'closed' or status == 'canceled':
                        if order_id in self.orders_cache:
                            del self.orders_cache[order_id]
                            self.logger.info(f"Order {order_id} ({symbol} {status}) removed from cache.")

                        if status == 'closed':
                            log_payload.update({'price': float(order.get('average') or '0'), 'amount': float(filled)})

                        await self.app['broadcast_log'](log_payload, self.name, self.logger)

                    await self.app['broadcast_orders_update'](self)

                    if status == 'closed' and side == 'buy':
                        last_filled_price = Decimal(order.get('average') or '0')

                        if asset in self.balances_cache and filled > 0:
                            old_total_amount = self.balances_cache[asset].get('total_amount', Decimal('0'))
                            old_avg_price = self.balances_cache[asset].get('avg_buy_price')

                            if old_avg_price is None or old_avg_price <= 0 or old_total_amount <= 0:
                                new_avg_price = last_filled_price
                            else:
                                old_cost = old_total_amount * old_avg_price
                                fill_cost = filled * last_filled_price
                                new_total_amount = old_total_amount + filled

                                if new_total_amount > 0:
                                    new_avg_price = (old_cost + fill_cost) / new_total_amount
                                else:
                                    new_avg_price = last_filled_price

                            self.balances_cache[asset]['avg_buy_price'] = new_avg_price
                            self.balances_cache[asset]['total_amount'] = old_total_amount + filled
                            self.logger.info(f"Average price for {asset} updated to {new_avg_price} by trade. Last fill: {filled} @ {last_filled_price}")

            except Exception as e:
                self.logger.error(f"Error in watch_orders_loop: {e}", exc_info=True)
                await asyncio.sleep(5)

    def _get_order_asset_names(self) -> Set[str]:
        """바이낸스 주문에서 자산 이름 추출"""
        return {order.get('symbol', '').replace(self.quote_currency, '').replace('/', '') for order in self.orders_cache.values()}