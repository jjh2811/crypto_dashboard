import logging
from decimal import Decimal
import json
import readline # don't remove this. this is for input()
from src.crypto_dashboard.utils.nlp.entity_extractor import EntityExtractor

# 기본 로거 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    """
    EntityExtractor 클래스를 테스트하기 위한 대화형 콘솔 애플리케이션입니다.
    """
    # 테스트용 가상 데이터 설정
    mock_coins = ["BTC", "ETH", "XRP", "DOGE", "SOL"]
    mock_config = {
        "intent_map": {
            "매수": "buy",
            "사": "buy",
            "사자": "buy",
            "매도": "sell",
            "팔": "sell",
            "팔자": "sell"
        },
        "custom_mapping": {
            "비트코인": "BTC",
            "이더리움": "ETH",
            "리플": "XRP",
            "도지": "DOGE",
            "솔라나": "SOL"
        },
        "quote_currency": "USDT"
    }

    # EntityExtractor 인스턴스 생성
    try:
        extractor = EntityExtractor(coins=mock_coins, config=mock_config, logger=logger)
        print("EntityExtractor가 성공적으로 초기화되었습니다.")
        print("테스트할 주문 문장을 입력하세요. (종료하려면 'exit' 또는 'quit' 입력)")
        print("-" * 30)
    except Exception as e:
        logger.error(f"EntityExtractor 초기화 중 오류 발생: {e}")
        return

    # 대화형 프롬프트 시작
    while True:
        try:
            input_text = input("> ")
            if input_text.lower() in ["exit", "quit"]:
                print("테스트를 종료합니다.")
                break

            if not input_text.strip():
                continue

            # 엔티티 추출
            entities = extractor.extract_entities(input_text)

            # Decimal 객체를 문자열로 변환하여 JSON 직렬화 지원
            def decimal_default(obj):
                if isinstance(obj, Decimal):
                    return str(obj)
                raise TypeError

            # 결과 출력
            print(json.dumps(entities, indent=4, default=decimal_default, ensure_ascii=False))
            print("-" * 30)

        except KeyboardInterrupt:
            print("\n테스트를 종료합니다.")
            break
        except Exception as e:
            logger.error(f"오류 발생: {e}")

if __name__ == "__main__":
    main()
