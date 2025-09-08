from typing import Any, Dict, List, Optional, TYPE_CHECKING

from ...utils.nlp.entity_extractor import EntityExtractor
from ...utils.nlp.trade_command_parser import TradeCommandParser

if TYPE_CHECKING:
    from ...exchange_coordinator import ExchangeCoordinator


class NlpTradeManager:
    """NLP 트레이딩 관리를 전담하는 서비스 클래스"""

    def __init__(self, coordinator: "ExchangeCoordinator"):
        self.coordinator = coordinator
        self.exchange = coordinator.exchange
        self.logger = coordinator.logger
        self.name = coordinator.name
        self.quote_currency = coordinator.quote_currency
        self.app = coordinator.app

        # NLP 컴포넌트 상태 - 리팩토링: PriceManager/OrderManager 직접 사용
        self.coins: List[str] = []
        self.parser: Optional[TradeCommandParser] = None

    async def initialize(self, nlptrade_config: Dict[str, Any]) -> None:
        """거래소의 코인 목록을 로드하고 NLP 관련 객체들을 초기화합니다."""
        try:
            self.logger.info(f"Initializing NLP trader for {self.name}...")

            # 마켓 로드 - 거래소에서 사용 가능한 코인 목록을 가져옴
            await self.exchange.load_markets(reload=True)

            # 활성 마켓 중 base가 있는 것만 추출하여 코인 목록 생성
            unique_coins = {
                market['base']
                for market in self.exchange.markets.values()
                if market.get('active') and market.get('base')
            }
            self.coins = sorted(list(unique_coins))
            self.logger.info(f"Loaded {len(self.coins)} unique coins for {self.name}.")

            # nlptrade 설정에 quote_currency 주입
            updated_nlptrade_config = nlptrade_config.copy()
            updated_nlptrade_config['quote_currency'] = self.quote_currency

            # NLP 컴포넌트 초기화 (리팩토링: PriceManager/OrderManager 직접 사용)
            extractor = EntityExtractor(self.coins, updated_nlptrade_config, self.logger)

            # TradeCommandParser에 필요한 인터페이스를 제공하는 mock 객체 생성
            # - PriceManager와 OrderManager 직접 주입
            class MockExchangeBase:
                def __init__(self, exchange, quote_currency, balances_cache, price_manager, order_manager):
                    self.exchange = exchange  # type: ignore
                    self.quote_currency = quote_currency  # type: ignore
                    self.balances_cache = balances_cache  # type: ignore
                    self.price_manager = price_manager  # 리팩토링 추가
                    self.order_manager = order_manager   # 리팩토링 추가

            # coordinator를 통해 PriceManager, OrderManager, BalanceManager 주입
            nlp_exchange_mock = MockExchangeBase(
                self.exchange,
                self.quote_currency,
                self.coordinator.balance_manager.balances_cache,
                self.coordinator.price_manager,
                self.coordinator.order_manager
            )
            self.parser = TradeCommandParser(extractor, nlp_exchange_mock, self.logger)
            self.logger.info(f"NLP trader initialized successfully for {self.name}.")

        except Exception as e:
            self.logger.error(f"Failed to initialize NLP trader for {self.name}: {e}", exc_info=True)
            raise

    def is_ready(self) -> bool:
        """NLP 컴포넌트가 준비되었는지 확인"""  # executor 제거 (리팩토링)
        return self.parser is not None

    async def parse_command(self, text: str):
        """자연어 텍스트를 파싱하여 거래 명령으로 변환"""
        if not self.parser:
            raise ValueError(f"NLP parser not available for exchange: {self.name}")

        return await self.parser.parse(text)

    async def execute_command(self, command):
        """거래 명령 실행"""  # 리팩토링: OrderManager 직접 사용
        if not self.parser:  # executor 대신 parser 확인
            raise ValueError(f"NLP parser not available for exchange: {self.name}")

        return await self.coordinator.order_manager.execute_trade_command(command)

    def get_available_coins(self) -> List[str]:
        """사용 가능한 코인 목록 반환"""
        return self.coins.copy()
