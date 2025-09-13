import pytest
from decimal import Decimal
import logging
from unittest.mock import AsyncMock, MagicMock

from src.crypto_dashboard.models.trade_models import TradeIntent
from src.crypto_dashboard.utils.nlp.entity_extractor import EntityExtractor
from src.crypto_dashboard.utils.nlp.trade_command_parser import TradeCommandParser

@pytest.fixture
def entity_extractor():
    coins = ["BTC", "ETH", "XRP"]
    config = {
        "intent_map": {"매수": "buy", "사": "buy", "매도": "sell", "팔아": "sell"},
        "custom_mapping": {"비트코인": "BTC", "이더리움": "ETH"},
        "quote_currency": "USDT"
    }
    return EntityExtractor(coins, config, logging.getLogger(__name__))

@pytest.fixture
def mock_exchange_base():
    mock = MagicMock()
    mock.quote_currency = "USDT"

    # Mock exchange attributes and methods
    mock.exchange.price_to_precision = MagicMock(side_effect=lambda symbol, price: f"{price:.2f}")
    mock.exchange.amount_to_precision = MagicMock(side_effect=lambda symbol, amount: f"{amount:.5f}")
    mock.exchange.load_markets = AsyncMock()
    # Start with some initial markets
    mock.exchange.markets = {
        'BTC/USDT': {'base': 'BTC', 'quote': 'USDT', 'active': True},
        'ETH/USDT': {'base': 'ETH', 'quote': 'USDT', 'active': True},
    }

    # Mock price_manager
    mock.price_manager.get_order_book = AsyncMock(return_value={'bid': 100.0, 'ask': 101.0})
    mock.price_manager.get_current_price = AsyncMock(return_value=100.5)

    # Mock balances_cache
    mock.balances_cache.get = MagicMock(return_value={'free': Decimal('10')})
    
    return mock

@pytest.fixture
def trade_command_parser(entity_extractor, mock_exchange_base):
    return TradeCommandParser(entity_extractor, mock_exchange_base, logging.getLogger(__name__))

@pytest.mark.asyncio
async def test_parse_simple_limit_buy(trade_command_parser):
    text = "buy 1 btc 90"
    command = await trade_command_parser.parse(text)
    assert isinstance(command, TradeIntent)
    assert command.intent == "buy"
    assert command.symbol == "BTC/USDT"
    assert command.amount == "1.00000"
    assert command.price == "90.00"
    assert command.order_type == "limit"

@pytest.mark.asyncio
async def test_parse_relative_price_and_stop_price(trade_command_parser):
    text = "buy 1 btc -10% stop -15%"
    command = await trade_command_parser.parse(text)
    assert isinstance(command, TradeIntent)
    assert command.intent == "buy"
    assert command.symbol == "BTC/USDT"
    assert command.amount == "1.00000"
    # base_price (bid) is 100, so -10% is 90
    assert command.price == "90.00"
    # base_price (bid) is 100, so -15% is 85
    assert command.stop_price == "85.00"
    assert command.order_type == "limit"

@pytest.mark.asyncio
async def test_parse_fixed_stop_price(trade_command_parser):
    text = "buy 1 btc 90 stop 85"
    command = await trade_command_parser.parse(text)
    assert isinstance(command, TradeIntent)
    assert command.intent == "buy"
    assert command.symbol == "BTC/USDT"
    assert command.amount == "1.00000"
    assert command.price == "90.00"
    assert command.stop_price == "85.00"
    assert command.order_type == "limit"

@pytest.mark.asyncio
async def test_parse_fixed_price_and_relative_stop_price(trade_command_parser):
    text = "buy 1 btc 90 stop -5%"
    command = await trade_command_parser.parse(text)
    assert isinstance(command, TradeIntent)
    assert command.intent == "buy"
    assert command.symbol == "BTC/USDT"
    assert command.amount == "1.00000"
    assert command.price == "90.00"
    # base_price (bid) is 100, so -5% is 95
    assert command.stop_price == "95.00"
    assert command.order_type == "limit"

@pytest.mark.asyncio
async def test_parse_relative_price_and_fixed_stop_price(trade_command_parser):
    text = "buy 1 btc -10% stop 85"
    command = await trade_command_parser.parse(text)
    assert isinstance(command, TradeIntent)
    assert command.intent == "buy"
    assert command.symbol == "BTC/USDT"
    assert command.amount == "1.00000"
    # base_price (bid) is 100, so -10% is 90
    assert command.price == "90.00"
    assert command.stop_price == "85.00"
    assert command.order_type == "limit"

@pytest.mark.asyncio
async def test_parse_stop_market_order(trade_command_parser):
    text = "sell 1 btc stop 105"
    command = await trade_command_parser.parse(text)
    assert isinstance(command, TradeIntent)
    assert command.intent == "sell"
    assert command.symbol == "BTC/USDT"
    assert command.amount == "1.00000"
    assert command.price is None
    assert command.stop_price == "105.00"
    assert command.order_type == "market" # Interpreted as a stop-market order

@pytest.mark.asyncio
async def test_parse_total_cost_order(trade_command_parser, mock_exchange_base):
    text = "buy btc for 1000 usdt"
    
    mock_exchange_base.price_manager.get_current_price = AsyncMock(return_value=100.0)
    
    command = await trade_command_parser.parse(text)
    assert isinstance(command, TradeIntent)
    assert command.intent == "buy"
    assert command.symbol == "BTC/USDT"
    # 1000 usdt / 100.0 price = 10
    assert command.amount == "10.00000"
    assert command.total_cost == "1000"
    assert command.order_type == "market"

@pytest.mark.asyncio
async def test_parse_relative_amount_order(trade_command_parser, mock_exchange_base):
    text = "sell 50% xrp"
    mock_exchange_base.balances_cache.get.return_value = {'free': Decimal('20')}

    command = await trade_command_parser.parse(text)
    assert isinstance(command, TradeIntent)
    assert command.intent == "sell"
    assert command.symbol == "XRP/USDT"
    # 50% of 20 is 10
    assert command.amount == "10.00000"
    assert command.order_type == "market"

@pytest.mark.asyncio
async def test_parse_fail_missing_intent(trade_command_parser):
    text = "1 btc 90"
    result = await trade_command_parser.parse(text)
    assert isinstance(result, str)
    assert "Missing intent or coin" in result

@pytest.mark.asyncio
async def test_parse_fail_missing_coin(trade_command_parser):
    text = "buy 1 90"
    result = await trade_command_parser.parse(text)
    assert isinstance(result, str)
    assert "Missing intent or coin" in result

@pytest.mark.asyncio
async def test_parse_fail_missing_amount(trade_command_parser):
    text = "buy btc"
    result = await trade_command_parser.parse(text)
    assert isinstance(result, str)
    assert "Missing amount information" in result

@pytest.mark.asyncio
async def test_market_refresh_on_unknown_coin(trade_command_parser, mock_exchange_base, entity_extractor):
    text = "buy 100 doge 0.1"
    
    # Initially, the extractor only knows about BTC, ETH, XRP
    assert "DOGE" not in entity_extractor.coins

    # Mock the load_markets to add the new coin to the markets dict
    async def mock_load_markets(reload=False):
        mock_exchange_base.exchange.markets['DOGE/USDT'] = {'base': 'DOGE', 'quote': 'USDT', 'active': True}

    mock_exchange_base.exchange.load_markets = AsyncMock(side_effect=mock_load_markets)

    command = await trade_command_parser.parse(text)

    # Assert that load_markets was called
    mock_exchange_base.exchange.load_markets.assert_called_once_with(reload=True)

    # Assert the command was parsed correctly after the refresh
    assert isinstance(command, TradeIntent), f"Parsing failed, returned: {command}"
    assert command.symbol == "DOGE/USDT"
    assert command.amount == "100.00000"
    assert command.price == "0.10"

@pytest.mark.asyncio
async def test_parse_oco_limit_stop_market_buy_order(trade_command_parser, mock_exchange_base):
    # Set current price to 100.5 for this test
    mock_exchange_base.price_manager.get_current_price = AsyncMock(return_value=100.5)
    
    # Buy order where price (95) < current_price (100.5) and stop_price (105) > current_price (100.5)
    text = "buy btc 10usdt 95 stop 105"
    command = await trade_command_parser.parse(text)
    
    assert isinstance(command, TradeIntent)
    assert command.intent == "buy"
    assert command.symbol == "BTC/USDT"
    # amount is calculated from total_cost (10) / price (95) = 0.10526
    assert command.amount == "0.10526" 
    assert command.price == "95.00"
    assert command.stop_price == "105.00"
    assert command.order_type == "oco_stop_market"

@pytest.mark.asyncio
async def test_parse_oco_limit_stop_market_sell_order(trade_command_parser, mock_exchange_base):
    # Set current price to 100.5 for this test
    mock_exchange_base.price_manager.get_current_price = AsyncMock(return_value=100.5)
    
    # Sell order where price (105) > current_price (100.5) and stop_price (95) < current_price (100.5)
    text = "sell 1 btc 105 stop 95"
    command = await trade_command_parser.parse(text)
    
    assert isinstance(command, TradeIntent)
    assert command.intent == "sell"
    assert command.symbol == "BTC/USDT"
    assert command.amount == "1.00000"
    assert command.price == "105.00"
    assert command.stop_price == "95.00"
    assert command.order_type == "oco_stop_market"

@pytest.mark.asyncio
async def test_parse_not_oco_buy_order(trade_command_parser, mock_exchange_base):
    # Set current price to 100.5 for this test
    mock_exchange_base.price_manager.get_current_price = AsyncMock(return_value=100.5)
    
    # This is a standard stop-limit order, not OCO, because price > current_price
    text = "buy 1 btc 102 stop 105"
    command = await trade_command_parser.parse(text)
    
    assert isinstance(command, TradeIntent)
    assert command.order_type == "limit" # Not "oco"

@pytest.mark.asyncio
async def test_parse_oco_stop_limit_buy_order(trade_command_parser, mock_exchange_base):
    # Set current price and order book specifically for this test
    mock_exchange_base.price_manager.get_current_price = AsyncMock(return_value=100000.0)
    mock_exchange_base.price_manager.get_order_book = AsyncMock(return_value={'bid': 100000.0, 'ask': 100001.0})
    
    # Buy order with stop_limit_price using 'limit +5'
    text = "buy 0.1 btc 95k stop 110000 limit +5"
    command = await trade_command_parser.parse(text)
    
    assert isinstance(command, TradeIntent)
    assert command.intent == "buy"
    assert command.symbol == "BTC/USDT"
    assert command.amount == "0.10000"
    assert command.price == "95000.00"
    assert command.stop_price == "110000.00"
    assert command.stop_limit_price == "105000.00" # 100000 * (1 + 5/100)
    assert command.order_type == "oco_stop_limit"
