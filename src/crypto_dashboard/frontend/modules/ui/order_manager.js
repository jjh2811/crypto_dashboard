// src/crypto_dashboard/frontend/modules/ui/order_manager.js

import { formatNumber } from '../utils/utils.js';
import {
    currentPrices,
    cachedOrders,
    valueFormats,
    activeExchange
} from '../data/data_store.js';

// DOM 요소 캐싱
const ordersContainer = document.getElementById("orders-container");

/**
 * 주문 카드 HTML을 생성합니다.
 * @param {object} order - 주문 데이터.
 * @param {number} currentPrice - 현재 가격.
 * @returns {string} 생성된 HTML 문자열.
 */
function createOrderCardHTML(order, currentPrice) {
    const side = (order.side || '').toUpperCase();
    const sideClass = side === 'BUY' ? 'side-buy' : 'side-sell';
    const orderDate = new Date(order.timestamp).toLocaleString();

    let marketCurrencyForDisplay;
    if (order.symbol && order.symbol.includes('/')) {
        marketCurrencyForDisplay = order.symbol.split('/')[1];
    } else {
        marketCurrencyForDisplay = '???';
    }
    const baseSymbol = order.symbol.includes('/') ? order.symbol.split('/')[0] : order.symbol;

    let priceDiffText = '-';
    let diffClass = '';
    if (currentPrice && order.price) {
        const priceDifference = order.price - currentPrice;
        const priceDiffPercent = currentPrice > 0 ? (priceDifference / currentPrice) * 100 : 0;

        if (priceDifference > 0) {
            diffClass = 'diff-positive';
        } else if (priceDifference < 0) {
            diffClass = 'diff-negative';
        } else {
            diffClass = 'profit-neutral';
        }
        priceDiffText = `${priceDiffPercent.toFixed(2)}%`;
    }

    const amount = parseFloat(order.amount) || 0;
    const filled = parseFloat(order.filled) || 0;
    const price = parseFloat(order.price) || 0;

    let amountText;
    let progress;

    if (side === 'BUY') {
        amountText = `${formatNumber(filled)} / ${formatNumber(amount)}`;
        progress = amount > 0 ? (filled / amount) * 100 : 0;
    } else { // SELL
        const unfilled = amount - filled;
        amountText = `${formatNumber(unfilled)} / ${formatNumber(amount)}`;
        progress = amount > 0 ? (unfilled / amount) * 100 : 0;
    }

    const unfilledValue = (amount - filled) * price;
    const orderDecimalPlaces = valueFormats[order.exchange] ?? 3;

    let stopPriceHTML = '';
    // 스탑 가격이 있고, 아직 트리거되지 않은 경우에만 Stop Price 행을 표시
    if (order.stop_price && !order.is_triggered) {
        let stopDiffText = '-';
        let stopDiffClass = '';
        if (currentPrice && order.stop_price) {
            const stopDifference = order.stop_price - currentPrice;
            const stopDiffPercent = currentPrice > 0 ? (stopDifference / currentPrice) * 100 : 0;
            if (stopDifference > 0) {
                stopDiffClass = 'diff-positive';
            } else if (stopDifference < 0) {
                stopDiffClass = 'diff-negative';
            } else {
                stopDiffClass = 'profit-neutral';
            }
            stopDiffText = `${stopDiffPercent.toFixed(2)}%`;
        }

        const label = "Stop Price:";

        stopPriceHTML = `
            <div class="info-row stop-price-row">
                <span class="info-label">${label}</span>
                <span class="info-value">${formatNumber(order.stop_price)} <span class="price-diff ${stopDiffClass}">(${stopDiffText})</span></span>
            </div>`;
    }

    let priceHTML = '';
    if (order.price !== null && order.price !== undefined && parseFloat(order.price) !== 0) {
        priceHTML = `
            <div class="info-row price-row">
                <span class="info-label">Price:</span>
                <span class="info-value">${formatNumber(order.price)} ${priceDiffText !== '-' ? `<span class="price-diff ${diffClass}">(${priceDiffText})</span>` : ''}</span>
            </div>`;
    }

    return `
        <div style="display: flex; align-items: center; justify-content: space-between;">
            <h2 style="margin: 0; flex-grow: 1; text-align: center;">${baseSymbol}</h2>
            <input type="checkbox" class="order-checkbox" data-order-id="${order.id}" data-symbol="${order.symbol}">
        </div>
        <div class="info-row">
            <span class="info-label">Side:</span>
            <span class="info-value ${sideClass}">${side}</span>
        </div>
        ${priceHTML}
        ${stopPriceHTML}
        <div class="info-row">
            <span class="info-label">Market:</span>
            <span class="info-value">${marketCurrencyForDisplay}</span>
        </div>
        <div class="info-row">
            <span class="info-label">Amount:</span>
            <span class="info-value amount-value">${amountText}</span>
        </div>
        <div class="progress-bar-container">
            <div class="progress-bar" style="width: ${progress}%;"></div>
        </div>
        <div class="info-row">
            <span class="info-label">Value:</span>
            <span class="info-value unfilled-value">${unfilledValue.toFixed(orderDecimalPlaces)}</span>
        </div>
        <div class="info-row">
            <span class="info-label">Date:</span>
            <span class="info-value">${orderDate}</span>
        </div>
    `;
}

/**
 * 주문 카드를 업데이트합니다.
 * @param {HTMLElement} card - 업데이트할 주문 카드 DOM 요소.
 * @param {object} order - 주문 데이터.
 * @param {number} currentPrice - 현재 가격.
 */
function updateOrderCard(card, order, currentPrice) {
    const { amount, filled, price, side, exchange, stop_price, is_triggered } = order;
    const orderDecimalPlaces = valueFormats[exchange] ?? 3;

    // 스탑 주문 상태에 따라 카드 스타일 업데이트
    const isPendingStop = stop_price && !is_triggered;
    if (isPendingStop) {
        card.classList.add('order-card--pending-stop');
    } else {
        card.classList.remove('order-card--pending-stop');
    }

    // Update Stop Price Row
    let stopPriceRow = card.querySelector('.stop-price-row');
    if (isPendingStop) {
        if (!stopPriceRow) {
            // Add the stop price row if it doesn't exist
            const priceRow = card.querySelector('.price-row');
            if (priceRow) {
                stopPriceRow = document.createElement('div');
                stopPriceRow.className = 'info-row stop-price-row';
                priceRow.parentNode.insertBefore(stopPriceRow, priceRow.nextSibling);
            }
        }
        if (stopPriceRow) {
            let stopDiffText = '-';
            let stopDiffClass = '';
            if (currentPrice && stop_price) {
                const stopDifference = stop_price - currentPrice;
                const stopDiffPercent = currentPrice > 0 ? (stopDifference / currentPrice) * 100 : 0;
                if (stopDifference > 0) {
                    stopDiffClass = 'diff-positive';
                } else if (stopDifference < 0) {
                    stopDiffClass = 'diff-negative';
                } else {
                    stopDiffClass = 'profit-neutral';
                }
                stopDiffText = `${stopDiffPercent.toFixed(2)}%`;
            }
            const label = "Stop Price:";
            stopPriceRow.innerHTML = `
                <span class="info-label">${label}</span>
                <span class="info-value">${formatNumber(stop_price)} <span class="price-diff ${stopDiffClass}">(${stopDiffText})</span></span>
            `;
        }
    } else if (stopPriceRow) {
        // 스탑 주문이 아니거나 트리거된 경우, Stop Price 행을 제거
        stopPriceRow.remove();
    }

    // Update Price Difference in price row
    const priceDiffSpan = card.querySelector('.price-row .price-diff');
    if (priceDiffSpan && currentPrice) {
        let diffClass = '';
        const priceDifference = price - currentPrice;
        if (priceDifference > 0) {
            diffClass = 'diff-positive';
        } else if (priceDifference < 0) {
            diffClass = 'diff-negative';
        } else {
            diffClass = 'profit-neutral';
        }
        priceDiffSpan.className = `price-diff ${diffClass}`;
        const priceDiffPercent = currentPrice > 0 ? (priceDifference / currentPrice) * 100 : 0;
        priceDiffSpan.textContent = `(${priceDiffPercent.toFixed(2)}%)`;
    }

    // Update Amount and Progress Bar
    const amountElement = card.querySelector('.amount-value');
    const progressBarElement = card.querySelector('.progress-bar');
    if (amountElement && progressBarElement) {
        const totalAmount = parseFloat(amount) || 0;
        const filledAmount = parseFloat(filled) || 0;
        let amountText;
        let progress;

        if (side.toUpperCase() === 'BUY') {
            amountText = `${formatNumber(filledAmount)} / ${formatNumber(totalAmount)}`;
            progress = totalAmount > 0 ? (filledAmount / totalAmount) * 100 : 0;
        } else { // SELL
            const unfilled = totalAmount - filledAmount;
            amountText = `${formatNumber(unfilled)} / ${formatNumber(totalAmount)}`;
            progress = totalAmount > 0 ? (unfilled / totalAmount) * 100 : 0;
        }
        amountElement.textContent = amountText;
        progressBarElement.style.width = `${progress}%`;
    }

    // Update Unfilled Value
    const valueElement = card.querySelector('.unfilled-value');
    if (valueElement) {
        const unfilledValue = (parseFloat(amount) - parseFloat(filled)) * parseFloat(price);
        valueElement.textContent = unfilledValue.toFixed(orderDecimalPlaces);
    }
}

/**
 * 주문 목록의 가격 차이를 업데이트합니다.
 */
export function updatePriceDiffs() {
    ordersContainer.querySelectorAll('.crypto-card[data-order-id]').forEach(card => {
        const orderId = card.dataset.orderId;
        const order = cachedOrders.find(o => o.id.toString() === orderId);
        if (!order) return;

        const currentPrice = currentPrices[order.symbol];
        const priceDiffElement = card.querySelector('.price-row .price-diff');
        const stopPriceDiffElement = card.querySelector('.stop-price-row .price-diff');

        // Update price diff
        if (currentPrice && priceDiffElement) {
            const priceDifference = order.price - currentPrice;
            const priceDiffPercent = currentPrice > 0 ? (priceDifference / currentPrice) * 100 : 0;

            let diffClass;
            if (priceDifference > 0) {
                diffClass = 'diff-positive';
            } else if (priceDifference < 0) {
                diffClass = 'diff-negative';
            } else {
                diffClass = 'profit-neutral';
            }

            priceDiffElement.className = `price-diff ${diffClass}`;
            priceDiffElement.textContent = `(${priceDiffPercent.toFixed(2)}%)`;
        }

        // Update stop price diff
        if (currentPrice && stopPriceDiffElement && order.stop_price) {
            const stopDifference = order.stop_price - currentPrice;
            const stopDiffPercent = currentPrice > 0 ? (stopDifference / currentPrice) * 100 : 0;

            let stopDiffClass;
            if (stopDifference > 0) {
                stopDiffClass = 'diff-positive';
            } else if (stopDifference < 0) {
                stopDiffClass = 'diff-negative';
            } else {
                stopDiffClass = 'profit-neutral';
            }

            stopPriceDiffElement.className = `price-diff ${stopDiffClass}`;
            stopPriceDiffElement.textContent = `(${stopDiffPercent.toFixed(2)}%)`;
        }
    });
}

/**
 * 주문 목록을 업데이트합니다.
 */
export function updateOrdersList() {
    const filteredOrders = cachedOrders.filter(order => order.exchange === activeExchange);
    const orderIdsOnScreen = new Set(Array.from(ordersContainer.querySelectorAll('.crypto-card[data-order-id]')).map(card => card.dataset.orderId));
    const incomingOrderIds = new Set(filteredOrders.map(order => order.id.toString()));

    // Remove orders that are no longer in the list
    orderIdsOnScreen.forEach(id => {
        if (!incomingOrderIds.has(id)) {
            const cardToRemove = ordersContainer.querySelector(`.crypto-card[data-order-id='${id}']`);
            if (cardToRemove) {
                cardToRemove.remove();
            }
        }
    });

    if (filteredOrders.length === 0) {
        ordersContainer.innerHTML = "<p>현재 활성화된 주문이 없습니다.</p>";
        return;
    }

    // Add or update orders
    filteredOrders.forEach(order => {
        const orderId = order.id.toString();
        let orderCard = ordersContainer.querySelector(`.crypto-card[data-order-id='${orderId}']`);
        const currentPrice = currentPrices[order.symbol];

        if (orderCard) {
            // Order exists, update it
            updateOrderCard(orderCard, order, currentPrice);
        } else {
            // New order, create and append it
            const emptyState = ordersContainer.querySelector("p");
            if (emptyState) emptyState.remove();

            orderCard = document.createElement("div");
            orderCard.className = "crypto-card";
            // 스탑 주문이고 트리거되지 않은 경우, pending 클래스 추가
            if (order.stop_price && !order.is_triggered) {
                orderCard.classList.add('order-card--pending-stop');
            }
            orderCard.dataset.orderId = orderId;
            orderCard.dataset.exchange = order.exchange;
            orderCard.innerHTML = createOrderCardHTML(order, currentPrice);

            // Insert in sorted order (newest first)
            const timestamp = order.timestamp;
            let inserted = false;
            for (const child of ordersContainer.children) {
                const childTimestamp = cachedOrders.find(o => o.id.toString() === child.dataset.orderId)?.timestamp;
                if (childTimestamp && timestamp > childTimestamp) {
                    ordersContainer.insertBefore(orderCard, child);
                    inserted = true;
                    break;
                }
            }
            if (!inserted) {
                ordersContainer.appendChild(orderCard);
            }
        }
    });
}