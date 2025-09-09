"""
간소화된 메인 모듈
서버 부트스트랩 및 라우팅만 담당합니다.
"""
import json
import logging
import os
import bcrypt
from dotenv import load_dotenv

from aiohttp import web

from .utils.auth import (
    auth_middleware,
    get_secret_token,
    init_auth_secrets,
    login,
    logout,
)
from .utils.broadcast import (
    basic_broadcast_log,
    basic_broadcast_message,
    basic_broadcast_orders_update,
)
from .utils.server_lifecycle import on_cleanup, on_shutdown, on_startup
from .utils.web_handlers import handle_websocket, health_check_handler


async def index_handler(request):
    return web.FileResponse(os.path.join(os.path.dirname(__file__), 'frontend', 'index.html'))


@web.middleware
async def csp_middleware(request, handler):
    """Content Security Policy 헤더 적용"""
    response = await handler(request)
    if isinstance(response, web.Response):
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self';"
        )
    return response


def init_app():
    """애플리케이션 초기화"""
    load_dotenv()  # .env 파일에서 환경 변수 로드

    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,  # 디버깅 완료 후 INFO로 복원
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )
    logger = logging.getLogger("main")

    # 인증 시스템 초기화
    init_auth_secrets()

    # 앱 생성
    app = web.Application(middlewares=[auth_middleware, csp_middleware])
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)

    # 비밀번호 로드 (환경 변수에서)
    login_password = os.getenv('LOGIN_PASSWORD')
    if not login_password:
        logger.error("LOGIN_PASSWORD not found in environment variables.")
        os._exit(1)

    # 비밀번호가 유효한 bcrypt 해시인지 확인
    hashed_password = login_password.encode('utf-8')
    try:
        # bcrypt.checkpw는 해시 형식이 유효하지 않으면 ValueError를 발생시킵니다.
        # 임의의 비밀번호로 확인하여 해시 자체의 유효성만 검사합니다.
        bcrypt.checkpw(b'some_dummy_password_to_check_hash_validity', hashed_password)
    except ValueError:
        logger.error("The LOGIN_PASSWORD in your .env file is not a valid bcrypt hash. "
                     "Please use the hash_password.py script to generate a valid hash.")
        os._exit(1)

    app['login_password'] = hashed_password

    # 인증 토큰 설정
    app['SECRET_TOKEN'] = get_secret_token()

    # 브로드캐스트 함수들 준비
    app['broadcast_message'] = basic_broadcast_message
    app['broadcast_orders_update'] = basic_broadcast_orders_update
    app['broadcast_log'] = basic_broadcast_log

    # 라우팅 설정
    app.router.add_get('/ws', handle_websocket)
    app.router.add_get('/health', health_check_handler)
    # 루트 경로로 요청 시 index.html 서빙
    app.router.add_get('/', index_handler) # lambda 대신 index_handler 사용

    # 정적 파일 서빙
    # frontend 디렉토리의 모든 파일을 루트 경로에서 서빙 (index.html, style.css 등)
    app.router.add_static('/', path=os.path.join(os.path.dirname(__file__), 'frontend'))

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