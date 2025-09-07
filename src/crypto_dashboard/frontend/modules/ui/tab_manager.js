// src/crypto_dashboard/frontend/modules/ui/tab_manager.js

import {
    exchanges,
    activeExchange,
    setActiveExchange as setActiveExchangeData
} from '../data/data_store.js';

// DOM 요소 캐싱
const exchangeTabsContainer = document.getElementById("exchange-tabs");

/**
 * 거래소 탭을 생성합니다.
 */
export function createExchangeTabs() {
    exchangeTabsContainer.innerHTML = '';
    exchanges.forEach(exchange => {
        const tab = document.createElement('button');
        tab.className = 'exchange-tab-button';
        tab.textContent = exchange;
        tab.dataset.exchange = exchange;
        tab.onclick = () => setActiveExchange(exchange); // 클릭 이벤트 핸들러
        exchangeTabsContainer.appendChild(tab);
    });
}

/**
 * 활성 거래소를 설정하고 UI를 업데이트합니다.
 * @param {string} exchangeName - 활성화할 거래소 이름.
 */
export function setActiveExchange(exchangeName) {
    setActiveExchangeData(exchangeName); // data_store 업데이트

    document.querySelectorAll('.exchange-tab-button').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.exchange === exchangeName);
    });
    document.querySelectorAll('.crypto-card').forEach(card => {
        if (card.dataset.exchange) {
            card.style.display = card.dataset.exchange === exchangeName ? '' : 'none';
        }
    });
    // 여기서 다른 컴포넌트의 업데이트 함수들을 호출해야 하지만, 
    // 순환 의존성을 피하기 위해 이벤트 방출이나 콜백으로 처리.
    // 일단 임시로 import 사용.
    updateTotalValue();
    updateOrdersList();
    updateLogsList();
}

/**
 * 일반 탭 (보유 목록, 주문 목록, 로그)을 전환합니다.
 * @param {Event} evt - 이벤트 객체.
 * @param {string} tabName - 활성화할 탭의 ID.
 */
export function openTab(evt, tabName) {
    var i, tabcontent, tablinks;
    tabcontent = document.getElementsByClassName("tab-content");
    for (i = 0; i < tabcontent.length; i++) {
        tabcontent[i].style.display = "none";
    }
    tablinks = document.getElementsByClassName("tab-button");
    for (i = 0; i < tablinks.length; i++) {
        tablinks[i].className = tablinks[i].className.replace(" active", "");
    }
    document.getElementById(tabName).style.display = "block";
    evt.currentTarget.className += " active";
}

// 임시 import (나중에 개선)
import { updateTotalValue } from './crypto_card.js';
import { updateOrdersList } from './order_manager.js';
import { updateLogsList } from './log_manager.js';