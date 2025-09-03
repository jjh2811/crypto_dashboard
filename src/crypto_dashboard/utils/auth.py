"""
인증 처리 모듈
로그인/로그아웃 및 인증 미들웨어 기능을 제공합니다.
"""
import os
import secrets
from datetime import datetime, timezone
from aiohttp import web
import bcrypt # Add this import

# 전역 인증 관련 변수들 (main 모듈에서 공유)
SECRET_TOKEN = None
COOKIE_NAME = "auth_token"
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_TIME = 600  # 10 minutes
login_attempts = {}
last_login_attempt = {}


def init_auth_secrets():
    """인증 관련 설정 초기화"""
    global SECRET_TOKEN, COOKIE_NAME
    SECRET_TOKEN = secrets.token_hex(32)
    COOKIE_NAME = "auth_token"

    # SECRET_TOKEN이 초기화 되었는지 확인
    if SECRET_TOKEN is None:
        raise ValueError("SECRET_TOKEN 초기화 실패")

    # 전역 변수들 설정
    global login_attempts, last_login_attempt
    login_attempts = {}
    last_login_attempt = {}


async def login(request):
    """POST 요청 + 비밀번호 입력 후 쿠키 발급"""
    from .broadcast import get_clients  # 순환 임포트 방지 위해 여기서 import

    ip_address = request.transport.get_extra_info('peername')[0]
    current_time = datetime.now(timezone.utc).timestamp()
    error_message = ""
    button_disabled = ""

    login_path = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'login.html')
    try:
        with open(login_path, 'r') as f:
            login_html = f.read()
    except FileNotFoundError:
        return web.Response(text="Login page not found.", status=404)

    if ip_address in last_login_attempt and current_time - last_login_attempt[ip_address] < LOGIN_LOCKOUT_TIME:
        if login_attempts.get(ip_address, 0) >= MAX_LOGIN_ATTEMPTS:
            error_message = "너무 많은 로그인 시도를 했습니다. 잠시 후 다시 시도하세요."
            button_disabled = "disabled"
            return web.Response(text=login_html.replace("{{error_message}}", error_message).replace("{{button_disabled}}", button_disabled), status=429, content_type='text/html')

    if request.method == "POST":
        data = await request.post()
        password = data.get("password", "")

        # 비밀번호 확인 로직을 app에서 가져와야 함
        hashed_password = request.app.get('login_password') # This will now be the hashed password (bytes)
        if not hashed_password or not bcrypt.checkpw(password.encode('utf-8'), hashed_password):
            login_attempts[ip_address] = login_attempts.get(ip_address, 0) + 1
            last_login_attempt[ip_address] = current_time
            if login_attempts[ip_address] >= MAX_LOGIN_ATTEMPTS:
                error_message = "너무 많은 로그인 시도를 했습니다. 잠시 후 다시 시도하세요."
                button_disabled = "disabled"
                return web.Response(text=login_html.replace("{{error_message}}", error_message).replace("{{button_disabled}}", button_disabled), status=429, content_type='text/html')
            else:
                error_message = "비밀번호가 틀렸습니다."
                return web.Response(text=login_html.replace("{{error_message}}", error_message).replace("{{button_disabled}}", ""), status=401, content_type='text/html')

        login_attempts.pop(ip_address, None)
        last_login_attempt.pop(ip_address, None)

        resp = web.HTTPFound('/')

        if SECRET_TOKEN is not None:
            # 새로운 쿠키를 설정하기 전에 기존 쿠키들을 삭제
            resp.del_cookie(COOKIE_NAME)

            resp.set_cookie(
                COOKIE_NAME,
                SECRET_TOKEN,
                httponly=True,
                secure=True,
                samesite="Strict",
                max_age=86400
            )
        else:
            return web.Response(text="Authentication system not initialized.", status=500)

        return resp

    # GET request
    return web.Response(text=login_html.replace("{{error_message}}", "").replace("{{button_disabled}}", ""), status=200, content_type='text/html')


async def logout(request):
    """쿠키 삭제로 로그아웃"""
    resp = web.HTTPFound('/login')
    resp.del_cookie(COOKIE_NAME)
    return resp


@web.middleware
async def auth_middleware(request, handler):
    # 로그인/로그아웃 페이지 및 정적 파일은 예외
    if request.path in ("/login", "/logout", "/style.css", "/script.js"):
        return await handler(request)

    token = request.cookies.get(COOKIE_NAME)

    if SECRET_TOKEN is None:
        return web.HTTPFound('/login')

    # Use secrets.compare_digest to prevent timing attacks
    if token is None or not secrets.compare_digest(token, SECRET_TOKEN):
        return web.HTTPFound('/login')

    return await handler(request)


def get_secret_token():
    """시크릿 토큰 반환"""
    return SECRET_TOKEN