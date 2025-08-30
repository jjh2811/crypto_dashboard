import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional
import logging

from aiohttp import web

from .protocols import Balances
from .utils.exchange import BalanceManager, OrderManager, PriceManager, NlpTradeManager, EventHandler


class ExchangeCoordinator:
    """거래소 관련 서비스들을 조율하는 코디네이터 클래스"""

    def __init__(self, api_key: str, secret_key: str, app: web.Application, exchange_name: str):
        self.name = exchange_name
        self.logger = logging.getLogger(exchange_name)
        self.app = app
        self.config = app['config'].get('exchanges', {}).get(self.name.lower(), {})

        # 기본 설정
        self.quote_currency = self.config.get('quote_currency')
        self.follows = self.config.get('follows', [])
        self.testnet = False
        self.whitelist: List[str] = []

        # 교환 연결 생성
        self._create_exchange(api_key, secret_key)

        # 서비스들 초기화
        self._init_services()

        # tracked_assets (공유 상태)
        self.tracked_assets = set()

    def _create_exchange(self, api_key: str, secret_key: str) -> None:
        """거래소 connection 생성"""
        import ccxt.pro as ccxtpro

        if 'testnet' in self.config and self.config['testnet'].get('use', False):
            self.testnet = True
            self.whitelist = self.config['testnet'].get('whitelist', [])

        exchange_class = getattr(ccxtpro, self.name)
        self.exchange = exchange_class({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
                'warnOnFetchOpenOrdersWithoutSymbol': False,
            },
        })

        if self.testnet:
            self.exchange.set_sandbox_mode(True)

    def _init_services(self) -> None:
        """모든 서비스 초기화"""
        self.balance_manager = BalanceManager(
            self.exchange, self.logger, self.name, self.quote_currency, self.app
        )

        self.order_manager = OrderManager(
            self.exchange, self.logger, self.name, self.quote_currency, self.app
        )

        self.price_manager = PriceManager(
            self.exchange, self.logger, self.name, self.quote_currency, self.app, self.balance_manager
        )

        self.nlp_trade_manager = NlpTradeManager(
            self.exchange, self.logger, self.name, self.quote_currency, self.app
        )

        self.event_handler = EventHandler(
            self.name, self.exchange, self.balance_manager,
            self.order_manager, self.price_manager, self.app,
            self.logger, self.quote_currency, self.testnet, self.whitelist
        )

    async def get_initial_data(self) -> None:
        """초기 데이터 로드 (REST + 설정)"""
        try:
            # NLP 트레이더 초기화 (마켓 정보 로딩)
            nlptrade_config = self.app['config'].get('nlptrade', {})
            await self.nlp_trade_manager.initialize(nlptrade_config)

            # 잔고 및 주문 데이터 조달
            balance, open_orders = await asyncio.gather(
                self.exchange.fetch_balance(),
                self.exchange.fetch_open_orders()
            )

            # 주문 관리자에 주문들 초기화
            await self.order_manager.initialize_orders(open_orders)

            # 잔고 처리
            total_balances = {
                asset: float(Decimal(str(total)))
                for asset, total in balance.get('total', {}).items()
                if Decimal(str(total)) > 0
            }

            await self.balance_manager.process_initial_balances(balance, total_balances)

            # follow 자산들 추가
            for asset in self.follows:
                self.balance_manager.add_follow_asset(asset)

            # tracked_assets 업데이트
            self.tracked_assets = (
                set(self.balance_manager.balances_cache.keys()) |
                set(self.order_manager.get_order_asset_names()) |
                self.balance_manager.follows |
                {self.quote_currency}
            )

            # 가격 데이터 초기화
            await self.price_manager.initialize_prices_for_tracked_assets(self.tracked_assets)

            self.logger.info(f"Initialized {len(self.tracked_assets)} tracked assets")

        except Exception as e:
            self.logger.error(f"Failed to fetch initial data from {self.name}: {e}", exc_info=True)
            await self.exchange.close()
            raise

    # 외부 API (프론트엔드 호환성 유지)
    async def cancel_order(self, order_id: str, symbol: str) -> None:
        await self.order_manager.cancel_order(order_id, symbol)

    async def cancel_all_orders(self) -> None:
        await self.order_manager.cancel_all_orders()

    def create_balance_update_message(self, symbol: str, balance_data: Dict[str, Any]) -> Dict[str, Any]:
        return self.balance_manager.create_balance_update_message(symbol, balance_data)

    def get_coins(self) -> List[str]:
        return self.nlp_trade_manager.get_available_coins()

    def is_nlp_ready(self) -> bool:
        return self.nlp_trade_manager.is_ready()

    def get_exchange(self):
        return self.exchange

    # 이벤트 루프들 (웹소켓 감시)
    async def watch_balance_loop(self) -> None:
        await self.event_handler.watch_balance_loop()

    async def watch_orders_loop(self) -> None:
        await self.event_handler.watch_orders_loop()

    async def watch_tickers_loop(self) -> None:
        await self.event_handler.watch_tickers_loop()

    async def close(self) -> None:
        await self.exchange.close()

    # 프로퍼тив 위해
    @property
    def exchange(self):
        return self._exchange

    @exchange.setter
    def exchange(self, value):
        self._exchange = value