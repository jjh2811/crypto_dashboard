// src/crypto_dashboard/frontend/modules/event/event_handlers.js

import { checkSocketAndSend } from '../websocket/websocket.js';
import {
    showConfirmModal, hideConfirmModal, showAlertModal, hideAlertModal,
    openDetailsModal, hideDetailsModal, openTab
} from '../ui/ui_manager.js';
import { pendingNlpCommand, clearPendingNlpCommand, activeExchange } from '../data/data_store.js';
import { basicEscape } from '../utils/utils.js';

// DOM 요소 캐싱
const modal = document.getElementById("details-modal");
const closeButton = document.querySelector(".close-button");
const confirmYesBtn = document.getElementById("confirm-yes-btn");
const confirmNoBtn = document.getElementById("confirm-no-btn");
const alertOkBtn = document.getElementById("alert-ok-btn");
const cryptoContainer = document.getElementById("crypto-container");
const cancelSelectedBtn = document.getElementById("cancel-selected-btn");
const cancelAllBtn = document.getElementById("cancel-all-btn");
const ordersContainer = document.getElementById("orders-container");
const commandBar = document.getElementById('command-bar');
const commandInput = document.getElementById('command-input');

/**
 * 모든 이벤트 리스너를 초기화합니다.
 */
export function initializeEventListeners() {
    // 모달 닫기 버튼
    closeButton.onclick = () => {
        hideDetailsModal();
    };
    // 모달 외부 클릭 시 닫기
    window.onclick = (event) => {
        if (event.target == modal) {
            hideDetailsModal();
        }
    };

    // 암호화폐 카드 클릭 시 상세 모달 열기
    cryptoContainer.addEventListener('click', (event) => {
        const card = event.target.closest('.crypto-card');
        if (card && card.dataset.symbol) {
            openDetailsModal(card.dataset);
        }
    });

    // 선택 주문 취소 버튼
    cancelSelectedBtn.addEventListener("click", () => {
        const selectedOrders = [];
        document.querySelectorAll(".order-checkbox:checked").forEach(checkbox => {
            selectedOrders.push({
                id: checkbox.dataset.orderId,
                symbol: checkbox.dataset.symbol
            });
        });
        if (selectedOrders.length > 0) {
            checkSocketAndSend({ type: 'cancel_orders', orders: selectedOrders, exchange: activeExchange });
        } else {
            showAlertModal("취소할 주문을 선택하세요.");
        }
    });

    // 알림 모달 확인 버튼
    alertOkBtn.addEventListener("click", () => {
        hideAlertModal();
    });

    // 전체 주문 취소 버튼
    cancelAllBtn.addEventListener("click", () => {
        showConfirmModal("모든 주문을 취소하시겠습니까?");
    });

    // 확인 모달 - 예 버튼
    confirmYesBtn.addEventListener("click", () => {
        let payload;
        if (pendingNlpCommand) {
            payload = { type: 'nlp_execute', command: pendingNlpCommand, exchange: activeExchange };
            clearPendingNlpCommand();
        } else {
            payload = { type: 'cancel_all_orders', exchange: activeExchange };
        }
        checkSocketAndSend(payload);
        hideConfirmModal();
    });

    // 확인 모달 - 아니오 버튼
    confirmNoBtn.addEventListener("click", () => {
        clearPendingNlpCommand();
        hideConfirmModal();
    });

    // 주문 목록 카드 클릭 시 체크박스 토글
    ordersContainer.addEventListener('click', (event) => {
        const card = event.target.closest('.crypto-card');
        if (!card) return;

        const checkbox = card.querySelector('.order-checkbox');
        if (checkbox && event.target.tagName.toLowerCase() !== 'input') {
            checkbox.checked = !checkbox.checked;
        }

        if (checkbox) {
            card.classList.toggle('selected', checkbox.checked);
        }
    });

    // 명령 입력 바 (NLP)
    commandInput.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            const text = commandInput.value.trim();
            if (text) {
                if (checkSocketAndSend({ type: 'nlp_command', text: basicEscape(text), exchange: activeExchange })) {
                    commandInput.value = '';
                    commandInput.blur();
                }
            }
        }
    });

    // 가상 키보드 높이 조절 (모바일)
    if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', () => {
            const viewport = window.visualViewport;
            const keyboardHeight = window.innerHeight - viewport.height;
            commandBar.style.bottom = `${keyboardHeight}px`;
        });
    }

    // 탭 버튼 이벤트 리스너 동적 추가
    document.querySelectorAll(".tab-button").forEach(button => {
        button.addEventListener("click", (event) => {
            openTab(event, button.dataset.tabName); // data-tab-name 속성 사용
        });
    });
}
