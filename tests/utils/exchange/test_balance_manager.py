
import asyncio
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock

import pytest

from crypto_dashboard.utils.exchange.balance_manager import BalanceManager


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
        'config': {
            'exchanges': {
                'test_exchange': {
                    'value_decimal_places': 2
                }
            }
        }
    }
    coordinator.follows = set()
    coordinator.testnet = False
    coordinator.whitelist = []
    coordinator.update_tracked_assets_and_restart_watcher = AsyncMock()
    return coordinator


@pytest.fixture
def balance_manager(mock_coordinator):
    """Creates a BalanceManager instance with a mock coordinator."""
    return BalanceManager(mock_coordinator)

@pytest.mark.asyncio
async def test_add_balance_new_asset(balance_manager, mock_coordinator):
    """Tests adding a new asset to the balance."""
    asset = "BTC"
    total_amount = Decimal("1.0")
    free = Decimal("1.0")
    used = Decimal("0.0")

    balance_manager.add_balance(asset, total_amount, free, used)

    assert asset in balance_manager.balances_cache
    assert balance_manager.balances_cache[asset]['total_amount'] == total_amount
    assert balance_manager.balances_cache[asset]['free'] == free
    assert balance_manager.balances_cache[asset]['locked'] == used
    mock_coordinator.update_tracked_assets_and_restart_watcher.assert_called_once()

@pytest.mark.asyncio
async def test_handle_zero_balance_not_followed(balance_manager, mock_coordinator):
    """Tests handling zero balance for a non-followed asset."""
    asset = "ETH"
    balance_manager.balances_cache[asset] = {
        'free': Decimal('1.0'),
        'locked': Decimal('0.0'),
        'total_amount': Decimal('1.0'),
    }

    balance_manager.handle_zero_balance(asset)

    assert asset not in balance_manager.balances_cache
    mock_coordinator.app['broadcast_message'].assert_called_once_with({
        'type': 'remove_holding',
        'symbol': f"{asset}/{mock_coordinator.quote_currency}",
        'exchange': mock_coordinator.name
    })
    mock_coordinator.update_tracked_assets_and_restart_watcher.assert_called_once()

@pytest.mark.asyncio
async def test_handle_zero_balance_followed(balance_manager, mock_coordinator):
    """Tests handling zero balance for a followed asset."""
    asset = "XRP"
    mock_coordinator.follows.add(asset)
    balance_manager.balances_cache[asset] = {
        'free': Decimal('100'),
        'locked': Decimal('0.0'),
        'total_amount': Decimal('100'),
    }

    balance_manager.handle_zero_balance(asset)

    assert asset in balance_manager.balances_cache
    assert balance_manager.balances_cache[asset]['total_amount'] == Decimal('0')
    mock_coordinator.app['broadcast_message'].assert_called_once()
    mock_coordinator.update_tracked_assets_and_restart_watcher.assert_called_once()
