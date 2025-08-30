from abc import ABC, abstractmethod
import asyncio
from decimal import Decimal
import json
import logging
import os
from typing import Any, Dict, List, Optional, Set, cast

from aiohttp import web

from .utils.exchange.exchange_utils import calculate_average_buy_price
from .nlptrade import EntityExtractor, TradeCommandParser, TradeExecutor
from .protocols import Balances, ExchangeProtocol


class ExchangeBase(ABC):
    def __init__(self, api_key: str, secret_key: str, app: web.Application, exchange_name: str) -> None:
        self.name = exchange_name
        self.logger = logging.getLogger(exchange_name)
        self.app = app

        with open(os.path.join(os.path.dirname(__file__), 'config.json')) as f:
            self.config = json.load(f)

        exchange_config = self.config['exchanges'][self.name.lower()]
        self.quote_currency = exchange_config.get('quote_currency')
        self.follows = exchange_config.get('follows', [])

        self._exchange = self._create_exchange_instance(api_key, secret_key, exchange_config)

        self.balances_cache: Dict[str, Dict[str, Any]] = {}
        self.orders_cache: Dict[str, Dict[str, Any]] = {}
        self.order_prices: Dict[str, Decimal] = {}  # 주문 코인들의 가격 캐시
        self.tracked_assets = set()
        self._ws_id_counter = 1

        # NLP-related instances
        self.coins: List[str] = []
        self.parser: Optional[TradeCommandParser] = None
        self.executor: Optional[TradeExecutor] = None

    @abstractmethod
    def _create_exchange_instance(self, api_key: str, secret_key: str, exchange_config: Dict[str, Any]) -> ExchangeProtocol:
        raise NotImplementedError

    @property
    def exchange(self) -> ExchangeProtocol:
        return cast(ExchangeProtocol, self._exchange)

    def create_balance_update_message(self, symbol, balance_data):
        """잔고 정보로부터 클라이언트에게 보낼 업데이트 메시지를 생성합니다."""
        price = Decimal(str(balance_data.get('price', '0')))
        free_amount = balance_data.get('free', Decimal('0'))
        locked_amount = balance_data.get('locked', Decimal('0'))
        total_amount = free_amount + locked_amount
        value = price * total_amount
        avg_buy_price = balance_data.get('avg_buy_price')
        realised_pnl = balance_data.get('realised_pnl')

        unrealised_pnl = None
        if avg_buy_price is not None and price > 0:
            unrealised_pnl = (price - avg_buy_price) * total_amount

        message = {
            'type': 'balance_update',
            'exchange': self.name,
            'symbol': symbol,
            'price': float(price),
            'free': float(free_amount),
            'locked': float(locked_amount),
            'value': float(value),
            'avg_buy_price': float(avg_buy_price) if avg_buy_price is not None else None,
            'realised_pnl': float(realised_pnl) if realised_pnl is not None else None,
            'unrealised_pnl': float(unrealised_pnl) if unrealised_pnl is not None else None,
            'quote_currency': self.quote_currency
        }

        reference_prices = self.app.get('reference_prices', {})
        reference_time = self.app.get('reference_time')

        if reference_prices and self.name in reference_prices and symbol in reference_prices[self.name]:
            ref_price = Decimal(str(reference_prices[self.name][symbol]))
            if ref_price > 0:
                price_change_percent = (price - ref_price) / ref_price * 100
                message['price_change_percent'] = float(price_change_percent)
                message['reference_time'] = reference_time

        return message

    async def _initialize_nlp_trader(self) -> None:
        """거래소의 코인 목록을 로드하고 NLP 거래 관련 객체들을 초기화합니다."""
        try:
            self.logger.info(f"Initializing NLP trader for {self.name}...")
            await self.exchange.load_markets(reload=True)
            
            # `base`가 있는 활성 마켓의 `base` 심볼만 추출합니다.
            unique_coins = {market['base'] for market in self.exchange.markets.values() if market.get('active') and market.get('base')}
            self.coins = sorted(list(unique_coins))
            self.logger.info(f"Loaded {len(self.coins)} unique coins for {self.name}.")

            # nlptrade 설정 로드
            nlptrade_config = self.config.get('nlptrade', {})
            # 각 거래소의 quote_currency를 nlptrade 설정에 주입
            nlptrade_config['quote_currency'] = self.quote_currency

            extractor = EntityExtractor(self.coins, nlptrade_config, self.logger)
            self.executor = TradeExecutor(self.exchange, self.quote_currency, self.logger)
            self.parser = TradeCommandParser(extractor, self.executor, self, self.logger)
            self.logger.info(f"NLP trader initialized successfully for {self.name}.")

        except Exception as e:
            self.logger.error(f"Failed to initialize NLP trader for {self.name}: {e}", exc_info=True)

    async def get_initial_data(self) -> None:
        try:
            # NLP 트레이더를 먼저 초기화하여 마켓 정보를 로드합니다.
            await self._initialize_nlp_trader()

            balance, open_orders = await asyncio.gather(
                self.exchange.fetch_balance(),
                self.exchange.fetch_open_orders()
            )

            for order in open_orders:
                price = Decimal(str(order.get('price') or 0))
                amount = Decimal(str(order.get('amount') or 0))
                order_id = order.get('id')
                if order_id:
                    self.orders_cache[order_id] = {
                        'id': order_id, 'symbol': order.get('symbol', ''), 'side': order.get('side'),
                        'price': float(price), 'amount': float(amount), 'value': float(price * amount),
                        'quote_currency': (lambda s: s.split('/')[1] if s and '/' in s else self.quote_currency)(order.get('symbol', '')),
                        'timestamp': order.get('timestamp'), 'status': order.get('status')
                    }
            self.logger.info(f"Fetched {len(open_orders)} open orders at startup.")

            total_balances = {
                asset: float(Decimal(str(total))) for asset, total in balance.get('total', {}).items() if Decimal(str(total)) > 0
            }

            await self._process_initial_balances(balance, total_balances)

        except Exception as e:
            self.logger.error(f"Failed to fetch initial data from {self.name}: {e}")
            await self.exchange.close()
            raise

        holding_assets = set(self.balances_cache.keys())
        order_assets = {o['symbol'].replace(self.quote_currency, '').replace('/', '') for o in self.orders_cache.values() if o.get('symbol')}
        follow_assets = set(self.follows)
        self.tracked_assets = holding_assets | order_assets | follow_assets

        # Track favorite_assets for price monitoring

        # follows 코인을 위한 dummy balance 데이터 추가
        for asset in follow_assets:
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


        # 주문이 있는 코인들의 가격도 초기화 (동기적으로 실행)
        try:
            await self._initialize_price_for_tracked_assets()
            self.logger.info(f"Initialized prices for {len(self.tracked_assets)} tracked assets")
        except Exception as e:
            self.logger.warning(f"Failed to initialize prices for tracked assets: {e}")

    async def _process_initial_balances(self, balance: Balances, total_balances: Dict[str, float]):
        async def process_single_asset(asset: str, total_amount: float) -> None:
            """단일 자산의 평균 가격 계산 및 결과 처리"""
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

                # 각 자산 계산 완료 즉시 로그 출력 🎯
                self.logger.info(f"Asset: {asset}, Avg Buy Price: {avg_buy_price if avg_buy_price is not None else 'N/A'}, Realised PnL: {realised_pnl}")

            except Exception as e:
                self.logger.error(f"Error calculating avg price for {asset}: {e}")

        # 모든 자산을 병렬로 처리 (계산 완료 시점에 로그 출력)
        async with asyncio.TaskGroup() as tg:
            for asset, total_amount in total_balances.items():
                tg.create_task(process_single_asset(asset, total_amount))

    

    async def cancel_order(self, order_id: str, symbol: str) -> None:
        try:
            # Send cancelling log before the actual request
            await self.app['broadcast_log']({'status': 'Cancelling', 'symbol': symbol, 'order_id': order_id}, self.name, self.logger)

            # Send the actual cancel request
            cancelled_order = await self.exchange.cancel_order(order_id, symbol)
            self.logger.info(f"Successfully sent cancel request for order {order_id}")

            # Remove the order from cache immediately for UI update
            if order_id in self.orders_cache:
                del self.orders_cache[order_id]
                self.logger.info(f"Removed order {order_id} from cache after successful cancel request")

        except Exception as e:
            self.logger.error(f"Failed to cancel order {order_id}: {e}")
            await self.app['broadcast_log']({'status': 'Cancel Failed', 'symbol': symbol, 'order_id': order_id, 'reason': str(e)}, self.name, self.logger)

    async def cancel_all_orders(self) -> None:
        self.logger.info("Received request to cancel all orders.")
        all_orders = list(self.orders_cache.values())
        if not all_orders:
            self.logger.info("No open orders to cancel.")
            await self.app['broadcast_log']({'status': 'Info', 'message': 'No open orders to cancel.'}, self.name, self.logger)
            return

        await self.app['broadcast_log']({'status': 'Info', 'message': f'Cancelling all {len(all_orders)} orders.'}, self.name, self.logger)

        for order in all_orders:
            order_id = order.get('id')
            symbol = order.get('symbol', '')
            if not order_id or not symbol:
                continue
            try:
                await self.exchange.cancel_order(order_id, symbol)
                self.logger.info(f"Successfully sent cancel request for order {order_id}")
            except Exception as e:
                self.logger.error(f"Failed to cancel order {order_id}: {e}")
                await self.app['broadcast_log']({'status': 'Cancel Failed', 'symbol': symbol, 'order_id': order_id, 'reason': str(e)}, self.name, self.logger)

        self.orders_cache.clear()

        # Broadcast updated orders list to all clients immediately
        await self.app['broadcast_orders_update'](self)

    async def _update_asset_price(self, asset: str, price: Decimal) -> None:
        """Update asset price in cache and broadcast update"""
        if asset in self.balances_cache:
            self.balances_cache[asset]['price'] = price
            update_message = self.create_balance_update_message(asset, self.balances_cache[asset])
            await self.app['broadcast_message'](update_message)
        else:
            # 주문 코인의 가격은 별도의 캐시에 저장하고 price_update 메시지만 전송
            self.order_prices[asset] = price
            update_message = {'symbol': asset, 'price': float(price)}
            await self.app['broadcast_message'](update_message)

    async def _initialize_price_for_tracked_assets(self) -> None:
        """Initialize prices for all tracked assets using batch fetch"""
        assets_to_fetch = [a for a in self.tracked_assets if a != self.quote_currency]
        if not assets_to_fetch:
            return

        symbols = [f"{asset}/{self.quote_currency}" for asset in assets_to_fetch]

        try:
            # Try batch fetch first
            tickers = await self.exchange.fetch_tickers(symbols)
            for symbol, ticker in tickers.items():
                asset = symbol.split('/')[0]
                await self._update_asset_price(asset, Decimal(str(ticker.get('last', '0'))))
        except Exception:
            # Fallback to individual fetches
            for asset in assets_to_fetch:
                try:
                    symbol = f"{asset}/{self.quote_currency}"
                    ticker = await self.exchange.fetch_ticker(symbol)
                    await self._update_asset_price(asset, Decimal(str(ticker.get('last', '0'))))
                except Exception as e:
                    self.logger.warning(f"Failed to fetch price for {asset}: {e}")

    async def _fetch_and_update_price(self, symbol: str, asset: str) -> None:
        """Fetch current price and update balances_cache for accurate diff calculation"""
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            current_price = Decimal(str(ticker.get('last', '0')))
            if current_price > 0:
                if asset in self.balances_cache:
                    self.balances_cache[asset]['price'] = current_price
                    update_message = self.create_balance_update_message(asset, self.balances_cache[asset])
                    await self.app['broadcast_message'](update_message)
                else:
                    # If asset not in balances_cache, still broadcast price update
                    update_message = {'symbol': asset, 'price': float(current_price)}
                    await self.app['broadcast_message'](update_message)
                self.logger.debug(f"Fetched current price for {asset}: {current_price}")
        except Exception as e:
            self.logger.warning(f"Failed to fetch current price for {symbol}: {e}")

    def _handle_zero_balance(self, asset: str, is_existing: bool) -> None:
        """잔고가 0인 코인을 처리하는 공통 로직"""
        if not is_existing:
            return

        order_assets = self._get_order_asset_names()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        # 캐시 업데이트
        if asset in self.tracked_assets or asset in order_assets:
            # 잔고만 0으로 설정
            self.balances_cache[asset]['free'] = Decimal('0')
            self.balances_cache[asset]['locked'] = Decimal('0')
            self.balances_cache[asset]['total_amount'] = Decimal('0')
            self.logger.info(f"Asset zeroed but kept for tracking: {asset}")

            # 메시지 전송 (async)
            if loop is not None:
                update_message = self.create_balance_update_message(asset, self.balances_cache[asset])
                asyncio.create_task(self.app['broadcast_message'](update_message))

        else:
            # 완전 삭제
            del self.balances_cache[asset]
            self.logger.info(f"Asset completely removed: {asset}")

            # 제거 메시지 전송 (async)
            if loop is not None:
                remove_message = {'type': 'remove_holding', 'symbol': asset, 'exchange': self.name}
                asyncio.create_task(self.app['broadcast_message'](remove_message))

    def _get_order_asset_names(self) -> Set[str]:
        """주문에서 자산 이름들을 추출 (자식 클래스에서 구현)"""
        raise NotImplementedError("거래소별 주문 자산 추출 로직을 구현해야 합니다")

    @abstractmethod
    async def watch_tickers_loop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def watch_balance_loop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def watch_orders_loop(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        await self.exchange.close()
