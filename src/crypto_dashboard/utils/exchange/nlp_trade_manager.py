import logging
from typing import Any, Dict, List, Optional

from ...protocols import ExchangeProtocol
from ...utils.nlp.entity_extractor import EntityExtractor
from ...utils.nlp.trade_command_parser import TradeCommandParser
from ...utils.nlp.trade_executor import TradeExecutor


class NlpTradeManager:
    """NLP 트레이딩 관리를 전담하는 서비스 클래스"""

    def __init__(
        self,
        exchange: ExchangeProtocol,
        logger: logging.Logger,
        name: str,
        quote_currency: str,
        app: Any
    ):
        self.exchange = exchange
        self.logger = logger
        self.name = name
        self.quote_currency = quote_currency
        self.app = app

        # NLP 컴포넌트 상태
        self.coins: List[str] = []
        self.parser: Optional[TradeCommandParser] = None
        self.executor: Optional[TradeExecutor] = None

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

            # NLP 컴포넌트 초기화
            extractor = EntityExtractor(self.coins, updated_nlptrade_config, self.logger)
            self.executor = TradeExecutor(self.exchange, self.quote_currency, self.logger)

            # TradeCommandParser에 필요한 인터페이스를 제공하는 mock 객체 생성
            class MockExchangeBase:
                def __init__(self, exchange, quote_currency, balances_cache):
                    self.exchange = exchange  # type: ignore
                    self.quote_currency = quote_currency  # type: ignore
                    self.balances_cache = balances_cache  # type: ignore

            # 처음에는 빈 캐시로 시작, 나중에 업데이트
            nlp_exchange_mock = MockExchangeBase(self.exchange, self.quote_currency, {})
            self.parser = TradeCommandParser(extractor, self.executor, nlp_exchange_mock, self.logger)  # type: ignore
            self.logger.info(f"NLP trader initialized successfully for {self.name}.")

        except Exception as e:
            self.logger.error(f"Failed to initialize NLP trader for {self.name}: {e}", exc_info=True)
            raise

    def is_ready(self) -> bool:
        """NLP 컴포넌트가 준비되었는지 확인"""
        return self.parser is not None and self.executor is not None

    async def parse_command(self, text: str):
        """자연어 텍스트를 파싱하여 거래 명령으로 변환"""
        if not self.parser:
            raise ValueError(f"NLP parser not available for exchange: {self.name}")

        return await self.parser.parse(text)

    async def execute_command(self, command):
        """거래 명령 실행"""
        if not self.executor:
            raise ValueError(f"NLP executor not available for exchange: {self.name}")

        return await self.executor.execute(command)

    def get_available_coins(self) -> List[str]:
        """사용 가능한 코인 목록 반환"""
        return self.coins.copy()