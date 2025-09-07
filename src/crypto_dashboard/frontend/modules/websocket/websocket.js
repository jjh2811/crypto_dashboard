// src/crypto_dashboard/frontend/modules/websocket/websocket.js

import {
    updateCurrentPrices, updateCurrentPercentages, updateCachedOrders, addCachedLog, updateExchanges,
    updatePriceTrackedCoins, updateValueFormats, updateExchangeInfo, updateReferencePrices,
    setPendingNlpCommand, clearPendingNlpCommand,
        activeExchange, pendingNlpCommand, getExchangeInfo, getCurrentPrices, getCurrentPercentages
} from '../data/data_store.js';

import {
    renderCryptoCard, updateOrdersList, updateLogsList,
    createExchangeTabs, setActiveExchange, showConfirmModal, showAlertModal, hideAlertModal
} from '../ui/ui_manager.js';

import { updatePriceDiffs } from '../ui/order_manager.js';

import { formatTradeCommandForConfirmation } from '../utils/utils.js';

let websocket;
let reconnectTimeout;

/**
 * WebSocket 연결을 시도합니다.
 */
export function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    websocket = new WebSocket(`${protocol}//${window.location.host}/ws`);

    websocket.onopen = () => {
        console.log("WebSocket connection established");
        if (reconnectTimeout) {
            clearTimeout(reconnectTimeout);
            reconnectTimeout = null;
        }
    };

    websocket.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            switch (data.type) {
                case 'exchanges_list':
                    updateExchanges(data.data);
                    createExchangeTabs();
                    if (data.data.length > 0) {
                        setActiveExchange(data.data[0]);
                    }
                    break;
                case 'follow_coins':
                    updatePriceTrackedCoins(data.exchange, data.follows);
                    console.log(`Received follow coins for ${data.exchange}:`, data.follows);
                    break;
                case 'value_format':
                    updateValueFormats(data.exchange, data.value_decimal_places);
                    updateExchangeInfo(data.exchange, data.quote_currency);
                    console.log(`Received config for ${data.exchange}:`, { value_decimal_places: data.value_decimal_places, quote_currency: data.quote_currency });
                    // Hardcode the price of the quote currency to 1
                    if (data.quote_currency) {
                        const marketSymbol = `${data.quote_currency}/${data.quote_currency}`;
                        updateCurrentPrices({ [marketSymbol]: 1.0 });
                    }
                    break;
                case 'portfolio_update':
                    const { symbol, exchange, free, locked, avg_buy_price, realised_pnl } = data; // symbol is "BTC"
                    const quoteCurrency = getExchangeInfo()[exchange]?.quoteCurrency;
                    if (!quoteCurrency) return;

                    const marketSymbol = `${symbol}/${quoteCurrency}`;
                    const uniqueId = `${exchange}_${marketSymbol}`;
                    const card = document.getElementById(uniqueId);

                    // Combine portfolio data with the latest price to calculate derived values
                    const price = getCurrentPrices()[marketSymbol] || (card ? parseFloat(card.dataset.price) : 0);
                    const value = price * (parseFloat(free) + parseFloat(locked));

                    const renderData = {
                        symbol: marketSymbol, // Full symbol for rendering
                        exchange,
                        free,
                        locked,
                        avg_buy_price,
                        realised_pnl,
                        price,
                        value,
                        // Preserve price_change_percent if it exists
                        price_change_percent: card ? card.dataset.price_change_percent : undefined
                    };
                    renderCryptoCard(renderData);
                    break;
                case 'remove_holding':
                    const quoteCurrencyRemove = getExchangeInfo()[data.exchange]?.quoteCurrency;
                    if (!quoteCurrencyRemove) return;
                    const marketSymbolRemove = `${data.symbol}/${quoteCurrencyRemove}`;
                    const uniqueIdRemove = `${data.exchange}_${marketSymbolRemove}`;
                    const cardToRemove = document.getElementById(uniqueIdRemove);
                    if (cardToRemove) {
                        cardToRemove.remove();
                        // updateTotalValue(); // renderCryptoCard에서 호출되므로 여기서는 필요 없음
                    }
                    break;
                case 'orders_update':
                    updateCachedOrders(data.data);
                    updateOrdersList();
                    break;
                case 'price_update':
                    updateCurrentPrices({ [data.symbol]: parseFloat(data.price) });
                    // percentage 데이터를 별도로 업데이트
                    updateCurrentPercentages({ [data.symbol]: parseFloat(data.percentage) });
                    updatePriceDiffs();

                    // Also trigger a re-render for the main crypto card
                    const uniqueIdPrice = `${data.exchange}_${data.symbol}`;
                    const cardPrice = document.getElementById(uniqueIdPrice);
                    if (cardPrice) {
                        const free = parseFloat(cardPrice.dataset.free || 0);
                        const locked = parseFloat(cardPrice.dataset.locked || 0);
                        const value = data.price * (free + locked);

                        const renderDataPrice = {
                            ...cardPrice.dataset, // Preserve all existing data
                            symbol: data.symbol,
                            price: data.price,
                            percentage: data.percentage, // 24시간 변화율 추가
                            value: value,
                        };
                        renderCryptoCard(renderDataPrice);
                    }
                    break;
                case 'log':
                    addCachedLog(data);
                    // Only prepend the new log if it belongs to the active exchange
                    if (data.exchange === activeExchange) {
                        updateLogsList(); // 전체 로그 목록을 다시 렌더링하여 최신 로그를 포함
                    }
                    break;
                case 'reference_price_info':
                    // UI 표시 없이 데이터만 저장 (가격 상대비율 계산용)
                    // updateReferencePriceInfo(data.time);  // UI 표시 함수는 호출하지 않음
                    updateReferencePrices(data.time, data.prices);
                    // 카드 재렌더링으로 상대비율 적용
                    document.querySelectorAll('#crypto-container .crypto-card').forEach(card => {
                        if (card.style.display !== 'none') {
                            // Re-rendering needs a complete data object.
                            // We reconstruct it from the card's dataset.
                            const marketSymbol = card.dataset.symbol;
                            const exchange = card.dataset.exchange;
                            const price = getCurrentPrices()[marketSymbol] || parseFloat(card.dataset.price || 0);
                            const free = parseFloat(card.dataset.free || 0);
                            const locked = parseFloat(card.dataset.locked || 0);
                            const value = price * (free + locked);

                            renderCryptoCard({
                                ...card.dataset,
                                price,
                                value
                            });
                        }
                    });
                    break;
                case 'nlp_trade_confirm':
                    showConfirmModal(formatTradeCommandForConfirmation(data.command));
                    setPendingNlpCommand(data.command);
                    break;
                case 'nlp_error':
                    showAlertModal(data.message);
                    break;
                default:
                    console.warn("Unknown message type:", data.type, data);
            }
        } catch (e) {
            console.error("Failed to parse JSON or process message:", e);
        }
    };

    websocket.onerror = (error) => {
        console.error("WebSocket error:", error);
    };

    websocket.onclose = (event) => {
        console.log("WebSocket connection closed:", event.code, event.reason);
        if (event.code === 1008 || event.code === 1001) {
            console.log("Not attempting to reconnect due to server-side close.");
            if (event.code === 1008) {
                alert("세션이 만료되었거나 인증에 실패했습니다. 다시 로그인해주세요.");
                window.location.href = "/login";
            }
        } else {
            console.log("Attempting to reconnect in 3 seconds...");
            reconnectTimeout = setTimeout(connectWebSocket, 3000);
        }
    };
}

/**
 * WebSocket이 연결되어 있는지 확인하고 메시지를 전송합니다.
 * @param {object} payload - 전송할 데이터 객체.
 * @returns {boolean} 메시지 전송 성공 여부.
 */
export function checkSocketAndSend(payload) {
    if (websocket && websocket.readyState === WebSocket.OPEN) {
        websocket.send(JSON.stringify(payload));
        return true;
    } else {
        showAlertModal("WebSocket is not connected. Please wait.");
        console.error("WebSocket is not open. State:", websocket ? websocket.readyState : 'null');
        return false;
    }
}
