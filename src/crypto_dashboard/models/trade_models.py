"""
거래 관련 데이터 모델 모듈
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class TradeCommand:
    """
    사용자 입력에서 파생된 구조화된 거래 명령을 나타냅니다.
    """
    intent: str  # "buy" or "sell"
    symbol: Optional[str]  # e.g., "BTC/USDT", "ETH/USDT"
    amount: Optional[str]  # 거래 수량
    price: Optional[str]  # 지정가 가격 (시장가의 경우 None)
    order_type: str  # "market" or "limit"
    stop_price: Optional[str] = None  # Stop 주문의 트리거 가격
    total_cost: Optional[str] = None  # 총 주문 비용