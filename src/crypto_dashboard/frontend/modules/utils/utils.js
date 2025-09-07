// src/crypto_dashboard/frontend/modules/utils/utils.js

/**
 * 숫자를 형식화하여 반환합니다. 소수점 이하 8자리까지 표시하며, 후행 0을 제거합니다.
 * @param {number} num - 형식화할 숫자.
 * @returns {string|number} 형식화된 숫자 문자열 또는 원본 값 (숫자가 아니거나 무한대일 경우).
 */
export function formatNumber(num) {
    if (typeof num !== 'number' || !isFinite(num)) return num;
    let numStr = num.toFixed(8);
    if (numStr.includes('.')) {
        numStr = numStr.replace(/0+$/, '');
        numStr = numStr.replace(/\.$/, '');
    }
    return numStr;
}

/**
 * 기본적인 HTML 이스케이핑을 수행합니다.
 * @param {string} text - 이스케이프할 텍스트.
 * @returns {string} 이스케이프된 텍스트.
 */
export function basicEscape(text) {
    if (typeof text !== 'string') return text;
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

/**
 * 거래 명령을 확인을 위한 HTML 형식으로 변환합니다.
 * @param {object} command - 거래 명령 객체.
 * @returns {string} 확인을 위한 HTML 문자열.
 */
export function formatTradeCommandForConfirmation(command) {
    const intentKr = command.intent === 'buy' ? '매수' : '매도';
    const orderTypeKr = command.order_type === 'market' ? '시장가' : '지정가';
    const coinSymbol = command.symbol && command.symbol.includes('/') ? command.symbol.split('/')[0] : (command.symbol || 'Unknown');

    let htmlParts = [
        '<div class="trade-confirmation">',
        '<h3>주문 확인</h3>',
        '<div class="confirmation-details">',
        `<div class="detail-row"><span class="detail-label">종류:</span><span class="detail-value intent-${command.intent}">${intentKr}</span></div>`,
        `<div class="detail-row"><span class="detail-label">코인:</span><span class="detail-value">${coinSymbol}</span></div>`,
        `<div class="detail-row"><span class="detail-label">주문 유형:</span><span class="detail-value">${orderTypeKr}</span></div>`,
    ];

    if (command.amount) {
        htmlParts.push(`<div class="detail-row"><span class="detail-label">수량:</span><span class="detail-value">${command.amount}</span></div>`);
    }

    if (command.price) {
        htmlParts.push(`<div class="detail-row"><span class="detail-label">지정가:</span><span class="detail-value">${command.price}</span></div>`);
    }

    if (command.stop_price) {
        htmlParts.push(`<div class="detail-row"><span class="detail-label">스탑 가격:</span><span class="detail-value">${command.stop_price}</span></div>`);
    }

    if (command.total_cost) {
        htmlParts.push(`<div class="detail-row"><span class="detail-label">총 주문액:</span><span class="detail-value">${command.total_cost}</span></div>`);
    }

    htmlParts.push(
        '</div>',
        '<div class="confirmation-notice">',
        '<p>이 주문을 정말로 실행하시겠습니까?</p>',
        '</div>',
        '</div>'
    );

    return htmlParts.join('');
}
