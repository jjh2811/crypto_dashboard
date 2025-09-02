"""
서버 라이프사이클 핸들러 모듈
서버 초기화, 종료 및 관련 작업들을 처리합니다.
"""
import asyncio
import json
import logging
import os
import secrets
from ..exchange_coordinator import ExchangeCoordinator





async def on_startup(app):
    """서버 시작 시 초기화 작업"""
    logger = logging.getLogger("server")
    logger.info("Server starting up...")

    # 브로드캐스트 함수들 초기화
    from .broadcast import init_broadcast_functions
    init_broadcast_functions(
        app['broadcast_message'],
        app['broadcast_orders_update'],
        app['broadcast_log']
    )

    config_path = os.path.join(os.path.dirname(__file__), '..', 'config.json')
    with open(config_path) as f:
        config = json.load(f)

    # app에 config 저장
    app['config'] = config
    app['subscription_lock'] = asyncio.Lock()
    app['exchanges'] = {}
    app['exchange_tasks'] = []
    app['reference_prices'] = {}
    app['reference_time'] = None

    exchanges_config = config.get('exchanges', {})
    if not exchanges_config:
        logger.error("No exchanges configured in config.json")
        return

    init_tasks = []
    pending_exchanges = []

    for exchange_name, exchange_config in exchanges_config.items():
        try:
            logger.info(f"Preparing to initialize exchange: {exchange_name}")

            is_testnet = exchange_config.get('testnet', {}).get('use', False)
            
            # 환경 변수 이름 생성 (e.g., EXCHANGE_BINANCE_API_KEY)
            base_env_name = f"EXCHANGE_{exchange_name.upper()}"
            if is_testnet:
                base_env_name += "_TESTNET"

            api_key_env = f"{base_env_name}_API_KEY"
            secret_key_env = f"{base_env_name}_SECRET_KEY"

            api_key = os.getenv(api_key_env)
            secret_key = os.getenv(secret_key_env)

            if not api_key or not secret_key:
                logger.error(f"API keys not found in environment variables for {base_env_name}")
                continue

            if f"YOUR_{exchange_name.upper()}" in api_key or f"YOUR_{exchange_name.upper()}" in secret_key:
                logger.warning(f"Please replace placeholder keys in .env for {base_env_name}.")
                continue

            exchange_instance = ExchangeCoordinator(api_key, secret_key, app, exchange_name)

            init_tasks.append(exchange_instance.get_initial_data())
            pending_exchanges.append(exchange_instance)

        except (FileNotFoundError, KeyError) as e:
            logger.error(f"Could not prepare {exchange_name} exchange due to missing secrets or config: {e}")
        except (ModuleNotFoundError, AttributeError) as e:
            logger.error(f"Could not load exchange module for '{exchange_name}': {e}")
        except Exception as e:
            logger.error(f"Error during {exchange_name} exchange preparation: {e}")

    if not init_tasks:
        logger.warning("No exchanges were prepared for initialization.")
        return

    logger.info(f"Initializing {len(init_tasks)} exchanges concurrently...")
    results = await asyncio.gather(*init_tasks, return_exceptions=True)

    for instance, result in zip(pending_exchanges, results):
        exchange_name = instance.name
        if isinstance(result, Exception):
            logger.error(f"Error during {exchange_name} exchange initialization: {result}")
        else:
            app['exchanges'][exchange_name] = instance
            balance_task = asyncio.create_task(instance.watch_balance_loop())
            orders_task = asyncio.create_task(instance.watch_orders_loop())
            app['exchange_tasks'].extend([balance_task, orders_task])
            logger.info(f"Successfully initialized and connected to {exchange_name}.")

    logger.info("All exchange initializations complete.")


async def on_shutdown(app):
    """서버 종료 시 작업"""
    logger = logging.getLogger("server")
    logger.info("Shutdown signal received. Closing client connections...")
    from .broadcast import get_clients
    clients = get_clients()
    for ws in list(clients):
        await ws.close(code=1001, message=b'Server shutdown')
    logger.info(f"All {len(clients)} client connections closed.")


async def on_cleanup(app):
    """정리 작업"""
    logger = logging.getLogger("server")
    logger.info("Cleaning up background tasks...")
    if 'exchange_tasks' in app:
        for task in app['exchange_tasks']:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    if 'exchanges' in app:
        for exchange_name, exchange in app['exchanges'].items():
            await exchange.close()
            logger.info(f"{exchange_name} exchange connection closed.")

    logger.info("All background tasks stopped.")
