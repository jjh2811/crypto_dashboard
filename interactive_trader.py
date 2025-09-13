import asyncio
import logging
import readline  # For better input() experience
import os
import json
from dotenv import load_dotenv
import ccxt.async_support as ccxt
from decimal import Decimal

from src.crypto_dashboard.utils.nlp.entity_extractor import EntityExtractor
from src.crypto_dashboard.utils.nlp.trade_command_parser import TradeCommandParser
from src.crypto_dashboard.models.trade_models import TradeIntent
from src.crypto_dashboard.protocols import ExchangeProtocol

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SimpleExchangeInterface:
    """A simple interface to provide necessary data for TradeCommandParser using REST APIs."""
    def __init__(self, exchange, quote_currency):
        self.exchange = exchange
        self.quote_currency = quote_currency
        self.price_manager = self  # Point to self to use methods below
        self.balances_cache = self # Point to self to use get() method

    async def get_order_book(self, symbol):
        try:
            # ccxt uses symbol with '/', e.g., 'BTC/USDT'
            market_symbol = f"{symbol.upper()}/{self.quote_currency}"
            if self.exchange.has['fetchOrderBook']:
                order_book = await self.exchange.fetch_order_book(market_symbol)
                return {'bid': order_book['bids'][0][0], 'ask': order_book['asks'][0][0]}
        except Exception as e:
            logger.error(f"Failed to fetch order book for {symbol}: {e}")
        return None

    async def get_current_price(self, symbol):
        try:
            market_symbol = f"{symbol.upper()}/{self.quote_currency}"
            if self.exchange.has['fetchTicker']:
                ticker = await self.exchange.fetch_ticker(market_symbol)
                return ticker.get('last')
        except Exception as e:
            logger.error(f"Failed to fetch current price for {symbol}: {e}")
        return None

    def get(self, symbol, default=None):
        # This mimics the balances_cache.get() call for relative amount calculation
        try:
            # This is a synchronous call inside an async function context, which is not ideal,
            # but for this simple script, we'll fetch it on demand.
            # A better approach would be to make this method async as well.
            logger.info(f"Fetching balance for {symbol} to calculate relative amount...")
            balance = asyncio.run(self.exchange.fetch_balance())
            base_symbol = symbol.upper()
            if base_symbol in balance:
                return {'free': Decimal(str(balance[base_symbol].get('free', 0)))}
        except Exception as e:
            logger.error(f"Failed to fetch balance for {symbol}: {e}")
        return default if default is not None else {}

async def main():
    """
    A simple, interactive console application to parse and execute trade commands.
    """
    load_dotenv()
    
    exchange_name = os.getenv("DEFAULT_EXCHANGE", "binance").lower()
    api_key_name = f"EXCHANGE_{exchange_name.upper()}_API_KEY"
    secret_key_name = f"EXCHANGE_{exchange_name.upper()}_SECRET_KEY"

    api_key = os.getenv(api_key_name)
    api_secret = os.getenv(secret_key_name)

    if not api_key or not api_secret:
        logger.error(f"{api_key_name} and {secret_key_name} must be set in the .env file.")
        return

    # Load configuration
    try:
        with open('src/crypto_dashboard/config.json', 'r') as f:
            app_config = json.load(f)
    except FileNotFoundError:
        logger.error("config.json not found.")
        return

    exchange_config = app_config.get('exchanges', {}).get(exchange_name, {})
    nlptrade_config = app_config.get('nlptrade', {})
    quote_currency = exchange_config.get('quote_currency', 'USDT')
    nlptrade_config['quote_currency'] = quote_currency

    # Initialize CCXT exchange using the factory
    from src.crypto_dashboard.utils.exchange.exchange_factory import get_exchange
    exchange: ExchangeProtocol = get_exchange(exchange_name, api_key, api_secret)

    try:
        # Load markets to get coin list for EntityExtractor
        await exchange.load_markets(reload=True)
        unique_coins = {m['base'] for m in exchange.markets.values() if m.get('active') and m.get('base')}
        
        # Initialize NLP components
        extractor = EntityExtractor(sorted(list(unique_coins)), nlptrade_config, logger)
        exchange_interface = SimpleExchangeInterface(exchange, quote_currency)
        parser = TradeCommandParser(extractor, exchange_interface, logger)

        print(f"Simple Trader is ready (Exchange: {exchange_name.capitalize()}).")
        print("Enter your trade command. Type 'exit' or 'quit' to terminate.")
        print("-" * 30)

        while True:
            input_text = await asyncio.to_thread(input, "> ")
            if input_text.lower() in ["exit", "quit"]:
                break
            if not input_text.strip():
                continue

            try:
                # 1. Parse the command
                intent = await parser.parse(input_text)
                
                if isinstance(intent, TradeIntent):
                    logger.info(f"Parsed Intent: {intent}")

                    # --- Type Guard ---
                    # Ensure all required fields for execution are present before proceeding.
                    if intent.symbol is None or intent.amount is None or intent.intent is None:
                        logger.error(f"Cannot execute trade: critical information missing. Intent: {intent}")
                        continue
                    
                    # 2. Ask for confirmation
                    confirm = await asyncio.to_thread(input, "Execute this command? (y/n): ")
                    if confirm.lower() == 'y':
                        # 3. Execute the command using the standardized methods
                        
                        # By this point, the type checker knows symbol and amount are not None.
                        if intent.is_oco:
                            if intent.price is None or intent.stop_price is None:
                                logger.error("OCO order requires both price and stop_price to be specified.")
                                continue
                            logger.info("OCO order detected. Using standardized 'create_oco_order'.")
                            order = await exchange.create_oco_order(
                                symbol=intent.symbol,
                                side=intent.intent,
                                amount=float(intent.amount),
                                price=float(intent.price),
                                stop_price=float(intent.stop_price),
                                stop_limit_price=float(intent.stop_limit_price) if intent.stop_limit_price else None,
                                params={}
                            )
                        else:
                            logger.info("Standard order detected. Using 'create_order'.")
                            params = {}
                            if intent.stop_price:
                                params['stopPrice'] = float(intent.stop_price)
                            
                            order = await exchange.create_order(
                                symbol=intent.symbol,
                                type=intent.order_type or 'limit', # Add a fallback for type safety
                                side=intent.intent,
                                amount=float(intent.amount),
                                price=float(intent.price) if intent.price else None,
                                params=params
                            )
                        
                        logger.info(f"Order execution result: {order}")
                    else:
                        logger.info("Execution cancelled.")
                else:
                    logger.warning(f"Parsing failed: {intent}")

            except Exception as e:
                logger.error(f"An error occurred: {e}")
            finally:
                print("-" * 30)

    finally:
        await exchange.close()
        logger.info("Exchange connection closed.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram terminated.")
