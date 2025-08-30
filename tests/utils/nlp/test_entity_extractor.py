
import pytest
from decimal import Decimal
import logging
from src.crypto_dashboard.utils.nlp.entity_extractor import EntityExtractor

@pytest.fixture
def entity_extractor():
    coins = ["BTC", "ETH", "XRP"]
    config = {
        "intent_map": {
            "매수": "buy",
            "사": "buy",
            "구매": "buy",
            "매도": "sell",
            "팔아": "sell",
            "판매": "sell"
        },
        "custom_mapping": {
            "비트코인": "BTC",
            "이더리움": "ETH",
            "리플": "XRP"
        },
        "quote_currency": "USDT"
    }
    logger = logging.getLogger(__name__)
    return EntityExtractor(coins, config, logger)

def test_extract_korean_buy_order(entity_extractor):
    text = "비트코인 1개 50000원에 매수"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "buy"
    assert entities["coin"] == "BTC"
    assert entities["amount"] == Decimal("1")
    assert entities["price"] == Decimal("50000")
    assert entities["order_type"] == "limit"

def test_extract_korean_sell_order_with_alias(entity_extractor):
    text = "이더리움 10개 팔아"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "sell"
    assert entities["coin"] == "ETH"
    assert entities["amount"] == Decimal("10")
    assert entities["price"] is None
    assert entities["order_type"] == "market"

def test_extract_english_buy_order(entity_extractor):
    text = "buy 0.5 XRP at 0.5"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "buy"
    assert entities["coin"] == "XRP"
    assert entities["amount"] == Decimal("0.5")
    assert entities["price"] == Decimal("0.5")
    assert entities["order_type"] == "limit"

def test_extract_english_market_sell_order(entity_extractor):
    text = "market sell 2 ETH"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "sell"
    assert entities["coin"] == "ETH"
    assert entities["amount"] == Decimal("2")
    assert entities["price"] is None
    assert entities["order_type"] == "market"

def test_extract_relative_amount_korean(entity_extractor):
    text = "리플 50% 매도"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "sell"
    assert entities["coin"] == "XRP"
    assert entities["relative_amount"] == "50"
    assert entities["amount"] is None

def test_extract_relative_amount_english(entity_extractor):
    text = "buy BTC 25%"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "buy"
    assert entities["coin"] == "BTC"
    assert entities["relative_amount"] == "25"
    assert entities["amount"] is None

def test_extract_total_cost_korean(entity_extractor):
    text = "비트코인 100000원어치 사"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "buy"
    assert entities["coin"] == "BTC"
    assert entities["total_cost"] == Decimal("100000")
    assert entities["amount"] is None

def test_extract_total_cost_english(entity_extractor):
    text = "buy ETH for 500 usdt"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "buy"
    assert entities["coin"] == "ETH"
    assert entities["total_cost"] == Decimal("500")
    assert entities["amount"] is None

def test_extract_current_price_order_korean(entity_extractor):
    text = "이더리움 현재가에 1개 매수"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "buy"
    assert entities["coin"] == "ETH"
    assert entities["amount"] == Decimal("1")
    assert entities["current_price_order"] is True
    assert entities["order_type"] == "limit"

def test_no_intent(entity_extractor):
    text = "비트코인 1개 50000원"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] is None

def test_no_coin(entity_extractor):
    text = "1개 50000원에 매수"
    entities = entity_extractor.extract_entities(text)
    assert entities["coin"] is None

def test_complex_korean_sentence(entity_extractor):
    text = "지금 시장 상황 보고 비트코인 0.5개 정도 60000 USDT에 팔아볼까?"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "sell"
    assert entities["coin"] == "BTC"
    assert entities["amount"] == Decimal("0.5")
    assert entities["price"] == Decimal("60000")
    assert entities["order_type"] == "limit"
