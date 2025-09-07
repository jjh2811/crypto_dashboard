// src/crypto_dashboard/frontend/modules/ui/modal_manager.js

import { formatNumber } from '../utils/utils.js';
import { valueFormats } from '../data/data_store.js';

// DOM 요소 캐싱
const modal = document.getElementById("details-modal");
const confirmModal = document.getElementById("confirm-modal");
const confirmModalText = document.getElementById("confirm-modal-text");
const alertModal = document.getElementById("alert-modal");
const alertModalText = document.getElementById("alert-modal-text");
const referenceTimeContainer = document.getElementById("reference-time-container");
const referenceTimeElement = document.getElementById("reference-time");

/**
 * 상세 모달을 엽니다.
 * @param {object} dataset - 암호화폐 카드 데이터셋.
 */
export function openDetailsModal(dataset) {
    const symbol = dataset.symbol;
    const baseSymbol = symbol.includes('/') ? symbol.split('/')[0] : symbol;
    const exchange = dataset.exchange;
    document.getElementById("modal-crypto-name").textContent = baseSymbol;
    const free = parseFloat(dataset.free || 0);
    const locked = parseFloat(dataset.locked || 0);
    const total = free + locked;
    const percentage = total > 0 ? ((free / total) * 100).toFixed(2) : 0;
    const realised_pnl = parseFloat(dataset.realised_pnl);
    // Use the pre-calculated unrealised_pnl from the dataset
    const unrealised_pnl = parseFloat(dataset.unrealised_pnl);

    const balanceDetailsContainer = document.getElementById("modal-crypto-balance-details");

    const formatPnl = (pnl, exchange) => {
        if (isNaN(pnl)) {
            return '<span class="info-value profit-neutral">-</span>';
        }
        const pnlClass = pnl >= 0 ? 'profit-positive' : 'profit-negative';
        const pnlSign = pnl > 0 ? '+' : '';
        const decimalPlaces = valueFormats[exchange] ?? 3;
        const formattedPnl = pnl.toLocaleString('en-US', {
            minimumFractionDigits: decimalPlaces,
            maximumFractionDigits: decimalPlaces
        });
        return `<span class="info-value ${pnlClass}">${pnlSign}${formattedPnl}</span>`;
    };

    balanceDetailsContainer.innerHTML = `
        <div class="info-row">
            <span class="info-label">Free:</span>
            <span class="info-value">${formatNumber(free)} / ${formatNumber(total)} (${percentage}%)</span>
        </div>
        <div class="info-row">
            <span class="info-label">Unrealised PnL:</span>
            ${formatPnl(unrealised_pnl, exchange)}
        </div>
        <div class="info-row">
            <span class="info-label">Realised PnL:</span>
            ${formatPnl(realised_pnl, exchange)}
        </div>
    `;

    // 모달에 현재 표시중인 코인의 ID 저장
    modal.dataset.currentCryptoId = `${exchange}_${symbol}`;

    modal.style.display = "block";
}

/**
 * 참조 가격 정보를 업데이트합니다.
 * @param {string} time - 참조 시간 문자열.
 */
export function updateReferencePriceInfo(time) {
    if (time) {
        const date = new Date(time);
        referenceTimeElement.textContent = date.toLocaleString();
        referenceTimeContainer.style.display = '';
    } else {
        referenceTimeContainer.style.display = 'none';
    }
}

/**
 * 확인 모달을 표시합니다.
 * @param {string} text - 표시할 텍스트.
 */
export function showConfirmModal(text) {
    confirmModalText.innerHTML = text;
    confirmModal.style.display = "block";
}

/**
 * 확인 모달을 숨깁니다.
 */
export function hideConfirmModal() {
    confirmModal.style.display = "none";
}

/**
 * 경고 모달을 표시합니다.
 * @param {string} text - 표시할 텍스트.
 */
export function showAlertModal(text) {
    alertModalText.textContent = text;
    alertModal.style.display = "block";
}

/**
 * 경고 모달을 숨깁니다.
 */
export function hideAlertModal() {
    alertModal.style.display = "none";
}

/**
 * 상세 모달을 숨깁니다.
 */
export function hideDetailsModal() {
    modal.style.display = "none";
}