"""
간소화된 메인 모듈
서버 부트스트랩 및 라우팅만 담당합니다.
"""
import json
import logging
import os

from aiohttp import web

from .utils.auth import auth_middleware, get_secret_token
from .utils.web_handlers import handle_websocket, http_handler
from .utils.server_lifecycle import on_startup, on_shutdown, on_cleanup
from .utils.broadcast import basic_broadcast_message, basic_broadcast_orders_update, basic_broadcast_log


def init_app():
    """애플리케이션 초기화"""
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )

    logger = logging.getLogger("main")

    # 앱 생성
    app = web.Application(middlewares=[auth_middleware])
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)

    # 비밀번호 로드
    secrets_path = os.path.join(os.path.dirname(__file__), 'secrets.json')
    with open(secrets_path) as f:
        secrets_data = json.load(f)

    login_password = secrets_data.get('login_password')
    if not login_password:
        logger.error("Login password not found in secrets.json under 'login_password' key. Please add it.")
        os._exit(1)

    app['login_password'] = login_password

    # 서버 설정 초기화 (모든 초기화 담당)
    from .utils.server_lifecycle import init_server_config
    init_server_config(login_password)

    # 인증 토큰 설정
    from .utils.auth import get_secret_token
    app['SECRET_TOKEN'] = get_secret_token()

    # 브로드캐스트 함수들 준비
    app['broadcast_message'] = basic_broadcast_message
    app['broadcast_orders_update'] = basic_broadcast_orders_update
    app['broadcast_log'] = basic_broadcast_log

    # 라우팅 설정
    app.router.add_get('/ws', handle_websocket)
    app.router.add_get('/', http_handler)
    app.router.add_get('/{filename}', http_handler)

    from .utils.auth import login, logout
    app.router.add_get('/login', login)
    app.router.add_post('/login', login)
    app.router.add_get('/logout', logout)

    return app


def main():
    """메인 함수"""
    logger = logging.getLogger("main")
    try:
        with open(os.path.join(os.path.dirname(__file__), 'config.json')) as f:
            config = json.load(f)
        host = config.get('host', 'localhost')
        port = config.get('port', 8000)
    except FileNotFoundError:
        host = 'localhost'
        port = 8000
        logger.warning("config.json not found, defaulting to host 'localhost' and port 8000")

    app = init_app()
    logger.info(f"Attempting to start server on http://{host}:{port}")
    web.run_app(app, host=host, port=port, access_log=None)
    logger.info("Server shutdown complete.")


if __name__ == "__main__":
    main()