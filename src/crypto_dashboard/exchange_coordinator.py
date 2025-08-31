import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set
import logging

from aiohttp import web

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
        self.follows = set(self.config.get('follows', []))
        self.testnet = False
        self.whitelist: List[str] = []

        # 교환 연결 생성
        self._create_exchange(api_key, secret_key)

        # 서비스들 초기화
        self._init_services()

        # tracked_assets (공유 상태)
        self.tracked_assets: Set[str] = set()
        self.price_watcher_task: Optional[asyncio.Task] = None
        self.watcher_restart_lock = asyncio.Lock()


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
        self.balance_manager = BalanceManager(self)
        self.order_manager = OrderManager(self)
        self.price_manager = PriceManager(self)
        self.nlp_trade_manager = NlpTradeManager(self)
        self.event_handler = EventHandler(self)


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

            # follow 자산들 추가 (testnet 시 무시)
            if not self.testnet:
                for asset in self.follows:
                    self.balance_manager.add_follow_asset(asset)

            # tracked_assets 업데이트 및 가격 데이터 초기화
            await self.update_tracked_assets_and_restart_watcher(is_initial=True)


        except Exception as e:
            self.logger.error(f"Failed to fetch initial data from {self.name}: {e}", exc_info=True)
            await self.exchange.close()
            raise

    async def update_tracked_assets_and_restart_watcher(self, is_initial: bool = False) -> None:
        """
        잔고, 주문, follows 목록을 기반으로 추적 자산 목록을 업데이트하고,
        이에 맞춰 Ticker 감시 루프를 재시작합니다.
        """
        async with self.watcher_restart_lock:
            self.logger.debug("Updating tracked assets and restarting watcher...")

            # 1. 새로운 추적 자산 목록 계산
            balance_assets = set(self.balance_manager.balances_cache.keys())
            order_assets = self.order_manager.get_order_asset_names()

            new_tracked_set = set(self.follows) | balance_assets | order_assets
            new_tracked_set.add(self.quote_currency) # 항상 포함

            # testnet 시 whitelist로 제한
            if self.testnet:
                allowed_assets = set(self.whitelist + [self.quote_currency])
                new_tracked_set &= allowed_assets

            old_tracked_set = self.tracked_assets

            # 변경 사항이 없으면 재시작 안함 (초기화 시에는 무조건 실행)
            if not is_initial and new_tracked_set == old_tracked_set:
                self.logger.debug("Tracked assets unchanged. Watcher not restarted.")
                return

            self.tracked_assets = new_tracked_set
            added_assets = new_tracked_set - old_tracked_set
            removed_assets = old_tracked_set - new_tracked_set

            if added_assets:
                self.logger.info(f"New assets to track: {added_assets}")
                await self.price_manager.initialize_prices_for_tracked_assets(added_assets)

            if removed_assets:
                self.logger.info(f"Stopped tracking assets: {removed_assets}")

            # 2. Ticker 감시 루프 재시작
            if self.price_watcher_task and not self.price_watcher_task.done():
                self.price_watcher_task.cancel()
                try:
                    await self.price_watcher_task
                except asyncio.CancelledError:
                    self.logger.info("Price watcher task cancelled successfully.")

            self.price_watcher_task = asyncio.create_task(self.watch_tickers_loop())
            self.logger.info(f"Price watcher started/restarted for {len(self.tracked_assets) -1} assets.")


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
        symbols = self.price_manager.get_tracked_symbols()
        if not symbols:
            self.logger.warning("No symbols to track, price watcher will not start.")
            return
        await self.price_manager.watch_tickers_loop(symbols)


    async def close(self) -> None:
        # 모든 감시 루프 태스크 취소
        for task in [self.price_watcher_task]:
             if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass # 정상 종료

        await self.exchange.close()
        self.logger.info("Exchange connection closed and all tasks cancelled.")


    # 프로퍼тив 위해
    @property
    def exchange(self):
        return self._exchange

    @exchange.setter
    def exchange(self, value):
        self._exchange = value
