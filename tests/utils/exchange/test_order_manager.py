
import asyncio
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock

import pytest

from crypto_dashboard.utils.exchange.order_manager import OrderManager


@pytest.fixture
def mock_coordinator():
    """Creates a mock ExchangeCoordinator with necessary attributes."""
    coordinator = MagicMock()
    coordinator.logger = MagicMock()
    coordinator.name = "test_exchange"
    coordinator.quote_currency = "USDT"
    coordinator.app = {
        'broadcast_message': AsyncMock(),
        'broadcast_orders_update': AsyncMock(),
        'broadcast_log': AsyncMock(),
        'exchanges': MagicMock()
    }
    coordinator.balance_manager = MagicMock()
    coordinator.balance_manager.update_average_price_on_buy = AsyncMock()
    coordinator.balance_manager.update_realized_pnl_on_sell = AsyncMock()
    coordinator.update_tracked_assets_and_restart_watcher = AsyncMock()
    coordinator.exchange = MagicMock()
    coordinator.exchange.cancel_order = AsyncMock()
    return coordinator


@pytest.fixture
def order_manager(mock_coordinator):
    """Creates an OrderManager instance with a mock coordinator."""
    return OrderManager(mock_coordinator)


@pytest.mark.asyncio
async def test_initialize_orders(order_manager):
    """Tests initializing the orders cache."""
    open_orders = [
        {
            'id': '1',
            'symbol': 'BTC/USDT',
            'side': 'buy',
            'price': '50000',
            'amount': '1',
            'filled': '0',
            'timestamp': 1616400000000,
            'status': 'open'
        }
    ]

    await order_manager.initialize_orders(open_orders)

    assert '1' in order_manager.orders_cache
    assert order_manager.orders_cache['1']['symbol'] == 'BTC/USDT'


@pytest.mark.asyncio
async def test_cancel_order(order_manager, mock_coordinator):
    """Tests cancelling an order."""
    order_id = "1"
    symbol = "BTC/USDT"

    await order_manager.cancel_order(order_id, symbol)

    mock_coordinator.exchange.cancel_order.assert_called_once_with(order_id, symbol)


@pytest.mark.asyncio
async def test_update_order_filled(order_manager, mock_coordinator):
    """Tests updating an order that has been filled."""
    order = {
        'id': '1',
        'symbol': 'BTC/USDT',
        'side': 'buy',
        'price': '50000',
        'amount': '1',
        'filled': '1',
        'average': '50000',
        'status': 'closed'
    }

    order_manager.orders_cache['1'] = {
        'id': '1',
        'symbol': 'BTC/USDT',
        'side': 'buy',
        'price': 50000.0,
        'amount': 1.0,
        'filled': 0.0,
        'value': 50000.0,
        'timestamp': 1616400000000,
        'status': 'open'
    }

    tasks = order_manager.update_order(order)
    await asyncio.gather(*tasks)

    assert '1' not in order_manager.orders_cache
    mock_coordinator.balance_manager.update_average_price_on_buy.assert_called_once()
    mock_coordinator.update_tracked_assets_and_restart_watcher.assert_called_once()
