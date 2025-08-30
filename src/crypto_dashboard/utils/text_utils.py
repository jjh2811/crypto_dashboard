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