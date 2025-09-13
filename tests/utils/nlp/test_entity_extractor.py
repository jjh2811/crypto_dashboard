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

def test_extract_english_stop_limit_order(entity_extractor):
    text = "limit buy 0.1 btc 50000 stop 49000"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "buy"
    assert entities["coin"] == "BTC"
    assert entities["amount"] == Decimal("0.1")
    assert entities["price"] == Decimal("50000")
    assert entities["stop_price"] == Decimal("49000")
    assert entities["order_type"] == "limit"

def test_extract_english_relative_price_and_relative_stop_price(entity_extractor):
    text = "limit buy btc 10usdt -10% stop -9%"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "buy"
    assert entities["coin"] == "BTC"
    assert entities["total_cost"] == Decimal("10")
    assert entities["relative_price"] == Decimal("-10")
    assert entities["relative_stop_price"] == Decimal("-9")
    assert entities["order_type"] == "limit"
    assert entities["price"] is None
    assert entities["stop_price"] is None

def test_extract_korean_relative_price_and_relative_stop_price(entity_extractor):
    text = "비트코인 10000원어치 -5%에 매수 stop -7%"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "buy"
    assert entities["coin"] == "BTC"
    assert entities["total_cost"] == Decimal("10000")
    assert entities["relative_price"] == Decimal("-5")
    assert entities["relative_stop_price"] == Decimal("-7")
    assert entities["order_type"] == "limit"

def test_extract_english_fixed_price_and_relative_stop_price(entity_extractor):
    text = "limit sell 1 eth 3000 stop -5%"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "sell"
    assert entities["coin"] == "ETH"
    assert entities["amount"] == Decimal("1")
    assert entities["price"] == Decimal("3000")
    assert entities["relative_stop_price"] == Decimal("-5")
    assert entities["order_type"] == "limit"

def test_extract_english_stop_limit_same_price(entity_extractor):
    text = "buy 0.1 btc 10000 stop 10000"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "buy"
    assert entities["coin"] == "BTC"
    assert entities["amount"] == Decimal("0.1")
    assert entities["price"] == Decimal("10000")
    assert entities["stop_price"] == Decimal("10000")
    assert entities["order_type"] == "limit"

def test_extract_korean_stop_limit_order_with_stopga(entity_extractor):
    text = "비트코인 1개 60000에 매수 스탑가 59000"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "buy"
    assert entities["coin"] == "BTC"
    assert entities["amount"] == Decimal("1")
    assert entities["price"] == Decimal("60000")
    assert entities["stop_price"] == Decimal("59000")
    assert entities["order_type"] == "limit"

def test_extract_english_oco_stop_limit_order_absolute(entity_extractor):
    text = "buy btc 10usdt 100k stop 110k limit 105k"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "buy"
    assert entities["coin"] == "BTC"
    assert entities["total_cost"] == Decimal("10")
    assert entities["price"] == Decimal("100000")
    assert entities["stop_price"] == Decimal("110000")
    assert entities["stop_limit_price"] == Decimal("105000")
    assert entities["relative_price"] is None
    assert entities["relative_stop_price"] is None
    assert entities["relative_stop_limit_price"] is None
    assert entities["order_type"] == "limit"

def test_extract_english_oco_stop_limit_order_relative(entity_extractor):
    text = "buy btc 10usdt -5% stop +5% limit +3%"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "buy"
    assert entities["coin"] == "BTC"
    assert entities["total_cost"] == Decimal("10")
    assert entities["price"] is None
    assert entities["stop_price"] is None
    assert entities["stop_limit_price"] is None
    assert entities["relative_price"] == Decimal("-5")
    assert entities["relative_stop_price"] == Decimal("5")
    assert entities["relative_stop_limit_price"] == Decimal("3")
    assert entities["order_type"] == "limit"

def test_extract_limit_keyword_not_in_oco_pattern(entity_extractor):
    text = "limit buy btc 10usdt 100k stop 105k"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "buy"
    assert entities["coin"] == "BTC"
    assert entities["total_cost"] == Decimal("10")
    assert entities["price"] == Decimal("100000")
    assert entities["stop_price"] == Decimal("105000")
    # Crucially, stop_limit_price should not be extracted
    assert entities["stop_limit_price"] is None
    assert entities["relative_stop_limit_price"] is None
    assert entities["order_type"] == "limit"

def test_extract_korean_oco_stop_limit_order(entity_extractor):
    # A complete OCO stop-limit order with a primary price, stop price, and stop-limit price.
    text = "비트코인 1개 45000에 매수 스탑 50000 지정가 49000"
    entities = entity_extractor.extract_entities(text)
    assert entities["intent"] == "buy"
    assert entities["coin"] == "BTC"
    assert entities["amount"] == Decimal("1")
    # The primary order price is 45000
    assert entities["price"] == Decimal("45000")
    # The OCO part
    assert entities["stop_price"] == Decimal("50000")
    assert entities["stop_limit_price"] == Decimal("49000")
    # The order type should be 'limit' because a primary price is specified.
    assert entities["order_type"] == "limit"
