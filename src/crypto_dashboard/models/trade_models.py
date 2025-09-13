"""
거래 관련 데이터 모델 모듈
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class TradeIntent:
    """
    자연어 처리(NLP)를 통해 사용자의 거래 의도를 파싱한 중간 데이터 모델입니다.
    이 모델은 실제 거래 실행 전 확인 단계에서 사용됩니다.
    """
    intent: str  # "buy" or "sell"
    symbol: Optional[str]  # e.g., "BTC/USDT", "ETH/USDT"
    amount: Optional[str]  # 거래 수량
    price: Optional[str]  # 지정가 가격 (시장가의 경우 None)
    order_type: str  # "market" or "limit"
    stop_price: Optional[str] = None  # Stop 주문의 트리거 가격
    stop_limit_price: Optional[str] = None  # OCO Stop-Limit 주문의 Limit 가격
    total_cost: Optional[str] = None  # 총 주문 비용
    is_oco: bool = False # OCO 주문 여부를 나타내는 플래그


@dataclass
class TradeCommand(TradeIntent):
    """
    사용자 입력에서 파생된 구조화된 거래 명령을 나타냅니다.
    TradeIntent를 상속받아 실제 거래에 필요한 추가 정보(예: 현재가)를 포함합니다.
    """
    current_price: Optional[float] = None # 주문 확인 시점의 현재가
    is_oco: bool = False # OCO 주문 여부를 나타내는 플래그
