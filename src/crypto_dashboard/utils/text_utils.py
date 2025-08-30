"""
텍스트 처리 유틸리티 모듈
"""
import unicodedata


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