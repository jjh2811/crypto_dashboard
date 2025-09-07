// src/crypto_dashboard/frontend/modules/main.js

import { connectWebSocket } from './websocket/websocket.js';
import { initializeEventListeners } from './event/event_handlers.js';
import { openTab } from './ui/ui_manager.js'; // openTab 함수는 초기 탭 활성화에 사용

document.addEventListener("DOMContentLoaded", () => {
    // WebSocket 연결 시작
    connectWebSocket();

    // 이벤트 리스너 초기화
    initializeEventListeners();

    // 초기 탭 활성화 (보유 목록 탭)
    // index.html에서 onclick을 제거했으므로, 여기서 첫 탭을 수동으로 활성화
    const initialTabButton = document.querySelector(".tab-button.active");
    if (initialTabButton) {
        // openTab 함수는 이벤트 객체를 첫 번째 인자로 받으므로, 가상의 이벤트 객체를 생성
        openTab({ currentTarget: initialTabButton }, initialTabButton.dataset.tabName);
    }
});
