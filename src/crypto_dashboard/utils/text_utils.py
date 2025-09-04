"""
텍스트 처리 유틸리티 모듈
"""
import unicodedata
import re
from decimal import Decimal, InvalidOperation

def expand_k_suffix(text: str) -> str:
    """
    숫자 뒤에 붙은 'k'를 1000을 곱한 값으로 변환합니다.
    예: 30k -> 30000, 2.67k -> 2670
    """
    def replacer(match):
        try:
            number_str = match.group(1)
            number = Decimal(number_str) * 1000
            # 정수로 변환 가능한 경우 정수로, 아니면 소수점으로 표현
            if number == number.to_integral_value():
                return str(number.to_integral_value())
            else:
                return str(number.normalize())
        except (InvalidOperation, IndexError):
            return match.group(0) # 변환 실패 시 원본 문자열 반환

    # 숫자와 'k'가 붙어있는 경우 (소수점 포함, 대소문자 구분 없음)
    return re.sub(r'(\d+(?:\.\d+)?)\s*k(?![a-zA-Z0-9])', replacer, text, flags=re.IGNORECASE)


def clean_text(text: str) -> str:
    """
    유효하지 않은 Unicode 문자를 제거하거나 대체합니다.
    Unicode 정규화 (NFKC: 호환성 문자 처리)를 수행한 후,
    유효하지 않은 문자(서로게이트 등)를 제거합니다.
    """
    # 유니코드 정규화 (NFKC: 호환성 문자 처리)
    text = unicodedata.normalize('NFKC', text)
    # 유효하지 않은 문자 (surrogate 등) 제거
    text = ''.join(c for c in text if c.isprintable() and ord(c) < 0x10000)
    return text


def sanitize_input(text: str) -> str:
    """
    기본적인 XSS 방지 및 입력 유효화를 수행합니다.
    - HTML 엔터티 이스케이핑
    - 입력 길이 제한
    - 빈 문자는 제거
    """
    if not isinstance(text, str) or len(text) > 500 or not text.strip():
        return ""

    # HTML 엔터티 이스케이핑
    text = text.replace('&', '&')
    text = text.replace('<', '<')
    text = text.replace('>', '>')
    text = text.replace('"', '"')
    text = text.replace("'", '&#x27;')

    return text.strip()