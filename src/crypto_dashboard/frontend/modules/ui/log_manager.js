// src/crypto_dashboard/frontend/modules/ui/log_manager.js

import { formatNumber } from '../utils/utils.js';
import { cachedLogs, activeExchange } from '../data/data_store.js';

// DOM 요소 캐싱
const logsContainer = document.getElementById("logs-container");

/**
 * 로그 메시지 요소를 생성합니다.
 * @param {object} data - 로그 데이터.
 * @returns {HTMLElement|null} 생성된 P 요소 또는 null (성공 로그의 경우).
 */
function createLogElement(data) {
    const logData = data.message;

    if (logData.status === 'success') {
        return null; // Do not display success logs
    }

    const logElement = document.createElement('p');
    const now = new Date(data.timestamp);
    const timestamp = `${now.getMonth() + 1}/${now.getDate()} ${now.toLocaleTimeString()}`;

    let messageText = `[${timestamp}]`;
    messageText += ` ${logData.status}`;

    if (logData.order_id) {
        messageText += ` [${logData.order_id}]`;
    }
    if (logData.symbol) {
        messageText += ` - ${logData.symbol}`;
    }
    if (logData.message) {
        messageText += ` - ${logData.message}`;
    }
    if (logData.side) {
        messageText += ` (${logData.side})`;
    }
    if (logData.price) {
        messageText += ` | Price: ${formatNumber(parseFloat(logData.price))}`;
    }
    if (logData.amount) {
        messageText += ` | Amount: ${formatNumber(parseFloat(logData.amount))}`;
    }

    // 스탑 주문 정보 표시
    if (logData.stop_price) {
        messageText += ` | Stop Price: ${formatNumber(parseFloat(logData.stop_price))}`;
        if (logData.is_triggered !== undefined) {
            messageText += logData.is_triggered ? ' (Triggered)' : ' (Not Triggered)';
        }
    }

    // 수수료 정보 추가
    if (logData.fee && logData.fee.cost > 0) {
        messageText += ` | Fee: ${formatNumber(logData.fee.cost)} ${logData.fee.currency}`;
    }
    if (logData.reason) {
        messageText += ` | Reason: ${logData.reason}`;
    }

    logElement.textContent = messageText;
    return logElement;
}

/**
 * 로그 목록을 업데이트합니다.
 */
export function updateLogsList() {
    logsContainer.innerHTML = "";
    const filteredLogs = cachedLogs.filter(log => log.exchange === activeExchange);

    // Iterate and prepend to show newest logs at the top
    filteredLogs.forEach(data => {
        const logElement = createLogElement(data);
        if (logElement) {
            logsContainer.appendChild(logElement);
        }
    });
}