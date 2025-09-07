// src/crypto_dashboard/frontend/modules/ui/ui_manager.js

import { formatNumber } from '../utils/utils.js';
import {
    currentPrices, cachedOrders, cachedLogs, exchanges, activeExchange,
    followCoins, valueFormats, exchangeInfo, referencePrices,
    setActiveExchange as setActiveExchangeData, updateExchanges as updateExchangesData
} from '../data/data_store.js';

// DOM 요소 캐싱
const cryptoContainer = document.getElementById("crypto-container");
const totalValueElement = document.getElementById("total-value");
const ordersContainer = document.getElementById("orders-container");
const logsContainer = document.getElementById("logs-container");
const referenceTimeContainer = document.getElementById("reference-time-container");
const referenceTimeElement = document.getElementById("reference-time");
const exchangeTabsContainer = document.getElementById("exchange-tabs");

const modal = document.getElementById("details-modal");
const confirmModal = document.getElementById("confirm-modal");
const confirmModalText = document.getElementById("confirm-modal-text");
const alertModal = document.getElementById("alert-modal");
const alertModalText = document.getElementById("alert-modal-text");

let totalValue = 0; // UI 계산용

/**
 * 암호화폐 카드 HTML을 생성합니다.
 * @param {object} data - 카드 렌더링에 필요한 데이터.
 * @returns {string} 생성된 HTML 문자열.
 */
function createCryptoCardHTML(data) {
    const symbol = data.symbol || 'Unknown';
    const baseSymbol = symbol.includes('/') ? symbol.split('/')[0] : symbol;
    const exchange = data.exchange;
    const price = Number.isFinite(parseFloat(data.price)) ? parseFloat(data.price) : 0;
    const value = Number.isFinite(parseFloat(data.value)) ? parseFloat(data.value) : 0;
    const roi = Number.isFinite(parseFloat(data.roi)) ? parseFloat(data.roi) : null;

    const decimalPlaces = valueFormats[exchange] ?? 3;

    let avgPriceText = '-';
    if (data.avg_buy_price && Number.isFinite(parseFloat(data.avg_buy_price))) {
        avgPriceText = formatNumber(parseFloat(data.avg_buy_price));
    }

    let roiText = '-';
    let roiClass = '';
    if (roi !== null) {
        roiText = `${roi.toFixed(2)}%`;
        roiClass = roi >= 0 ? 'profit-positive' : 'profit-negative';
    }

    let priceChangeClass = '';
    let priceChangeText = '';
    if (data.price_change_percent !== undefined) {
        const change = parseFloat(data.price_change_percent);
        priceChangeClass = change >= 0 ? 'profit-positive' : 'profit-negative';
        priceChangeText = `(${change.toFixed(2)}%)`;
    }

    const formattedValue = value.toLocaleString('en-US', {
        minimumFractionDigits: decimalPlaces,
        maximumFractionDigits: decimalPlaces
    });

    return `
        <h2>${baseSymbol}</h2>
        <div class="info-row">
            <span class="info-label">Price:</span>
            <span class="info-value">
                <span class="price-value">${formatNumber(price)}</span>
                <span class="price-change-percent ${priceChangeClass}">${priceChangeText}</span>
            </span>
        </div>
        <div class="info-row">
            <span class="info-label">Avg. Price:</span>
            <span class="info-value avg-price-value">${avgPriceText}</span>
        </div>
        <div class="info-row">
            <span class="info-label">ROI:</span>
            <span class="info-value roi-value ${roiClass}">${roiText}</span>
        </div>
        <div class="info-row value" data-value="${value}">
            <span class="info-label">Value:</span>
            <span class="info-value value-text">${formattedValue}</span>
        </div>
        <div class="info-row share">
            <span class="info-label">Share:</span>
            <span class="info-value share-value">-</span>
        </div>
    `;
}

/**
 * 암호화폐 카드를 렌더링하거나 업데이트합니다.
 * @param {object} data - 카드 데이터.
 */
export function renderCryptoCard(data) {
    const { symbol, price, exchange, value, avg_buy_price, free, locked } = data; // symbol is "BTC/USDT"
    const uniqueId = `${exchange}_${symbol}`;
    let card = document.getElementById(uniqueId);

    const decimalPlaces = valueFormats[exchange] ?? 3;

    // Calculate price_change_percent based on reference prices
    let price_change_percent = null;
    const baseSymbol = symbol.split('/')[0];
    if (referencePrices[exchange] && referencePrices[exchange][baseSymbol]) {
        const refPrice = referencePrices[exchange][baseSymbol];
        if (refPrice > 0) {
            price_change_percent = ((parseFloat(price) - refPrice) / refPrice) * 100;
        }
    }

    // Calculate Unrealized PnL and ROI here
    let unrealised_pnl = null;
    let roi = null;
    const avgPrice = parseFloat(avg_buy_price);
    const currentPrice = parseFloat(price);
    if (avgPrice > 0) {
        const totalAmount = parseFloat(free || 0) + parseFloat(locked || 0);
        unrealised_pnl = (currentPrice - avgPrice) * totalAmount;
        const costBasis = avgPrice * totalAmount;
        if (costBasis !== 0) {
            roi = (unrealised_pnl / costBasis) * 100;
        }
    }

    if (!card) {
        // Card does not exist, create it for the first time.
        card = document.createElement("div");
        card.id = uniqueId;
        card.className = "crypto-card";
        // Pass calculated values to the HTML creation function
        card.innerHTML = createCryptoCardHTML({ ...data, roi, price_change_percent });
        cryptoContainer.appendChild(card);
    }

    // Card exists, update only the necessary parts.
    const priceElement = card.querySelector(".price-value");
    const priceChangeElement = card.querySelector(".price-change-percent");
    const avgPriceElement = card.querySelector(".avg-price-value");
    const roiElement = card.querySelector(".roi-value");
    const valueContainer = card.querySelector(".value");
    const valueElement = card.querySelector(".value-text");

    // Update Price and Price Change
    if (priceElement) priceElement.textContent = formatNumber(currentPrice);
    if (priceChangeElement) {
        if (price_change_percent !== null) {
            const change = parseFloat(price_change_percent);
            const changeClass = change >= 0 ? 'profit-positive' : 'profit-negative';
            priceChangeElement.className = `price-change-percent ${changeClass}`;
            priceChangeElement.textContent = `(${change.toFixed(2)}%)`;
        } else {
            priceChangeElement.textContent = '';
        }
    }

    // Update Avg. Price and ROI
    if (avgPrice > 0 && roi !== null) {
        if (avgPriceElement) avgPriceElement.textContent = formatNumber(avgPrice);
        if (roiElement) {
            const profitClass = roi >= 0 ? 'profit-positive' : 'profit-negative';
            roiElement.className = `info-value roi-value ${profitClass}`;
            roiElement.textContent = `${roi.toFixed(2)}%`;
        }
    }

    // Update Value
    const formattedValue = parseFloat(value).toLocaleString('en-US', {
        minimumFractionDigits: decimalPlaces,
        maximumFractionDigits: decimalPlaces
    });
    if (valueContainer) valueContainer.dataset.value = value; // Store raw value
    if (valueElement) valueElement.textContent = formattedValue;

    // Update dataset for modal and other interactions
    Object.keys(data).forEach(key => {
        if (data[key] !== null && data[key] !== undefined) {
            card.dataset[key] = data[key];
        }
    });
    // Store calculated unrealised_pnl in dataset for the modal
    if (unrealised_pnl !== null) {
        card.dataset.unrealised_pnl = unrealised_pnl;
    }


    // Update styling for followed zero-balance coins
    const totalAmount = parseFloat(card.dataset.free || 0) + parseFloat(card.dataset.locked || 0);
    const isZeroBalance = totalAmount === 0;
    const baseAsset = symbol.split('/')[0];
    const is_follow = followCoins[exchange]?.has(baseAsset) || false;
    card.classList.toggle('follow-zero-balance', is_follow && isZeroBalance);

    // Hide if not in active exchange
    if (activeExchange && card.dataset.exchange !== activeExchange) {
        card.style.display = 'none';
    }

    updateTotalValue();
    updatePriceDiffs();

    // Update modal if it's open for this crypto
    if (modal.style.display === "block" && document.getElementById("modal-crypto-name").textContent === symbol.split('/')[0]) {
        const currentCryptoId = modal.dataset.currentCryptoId;
        const currentExchange = currentCryptoId ? currentCryptoId.split('_')[0] : null;
        if (currentExchange === exchange) {
            openDetailsModal(card.dataset);
        }
    }
}

/**
 * 총 보유 자산 가치를 업데이트합니다.
 */
export function updateTotalValue() {
    totalValue = 0;
    document.querySelectorAll('#crypto-container .crypto-card').forEach(el => {
        if (el.style.display !== 'none') {
            totalValue += parseFloat(el.querySelector('.value').dataset.value || 0);
        }
    });
    // 활성 거래소의 소수점 형식을 사용하여 내 가치 표시
    const decimalPlaces = valueFormats[activeExchange] ?? 3;
    totalValueElement.textContent = `${totalValue.toLocaleString('en-US', {
        minimumFractionDigits: decimalPlaces,
        maximumFractionDigits: decimalPlaces
    })}`;
    updateShares();
}

/**
 * 각 코인의 총 자산 대비 비중을 업데이트합니다.
 */
function updateShares() {
    if (totalValue === 0) return;

    document.querySelectorAll('#crypto-container .crypto-card').forEach(card => {
        if (card.style.display !== 'none') {
            const valueElement = card.querySelector('.value');
            const shareElement = card.querySelector('.share .info-value');

            if (valueElement && shareElement) {
                const cardValue = parseFloat(valueElement.dataset.value || 0);
                const share = (cardValue / totalValue) * 100;
                shareElement.textContent = `${share.toFixed(2)}%`;
            }
        }
    });
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
        const priceDiffElement = card.querySelector('.price-diff-value');

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

            priceDiffElement.className = `info-value price-diff-value ${diffClass}`;
            priceDiffElement.textContent = `${priceDiffPercent.toFixed(2)}%`;
        }
    });
}

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
    if (order.stop_price !== null && order.stop_price !== undefined && parseFloat(order.stop_price) !== 0) {
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
        stopPriceHTML = `
            <div class="info-row stop-price-row">
                <span class="info-label">Stop Price:</span>
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
    const { amount, filled, price, side, exchange, stop_price } = order;
    const orderDecimalPlaces = valueFormats[exchange] ?? 3;

    // Update Stop Price
    let stopPriceRow = card.querySelector('.stop-price-row');
    if (stop_price !== null && stop_price !== undefined && parseFloat(stop_price) !== 0) {
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
            stopPriceRow.innerHTML = `
                <span class="info-label">Stop Price:</span>
                <span class="info-value">${formatNumber(stop_price)} <span class="price-diff ${stopDiffClass}">(${stopDiffText})</span></span>
            `;
        }
    } else if (stopPriceRow) {
        // Remove the stop price row if it exists but is no longer needed
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
    if (logData.order_type === 'STOP' && logData.stop_price) {
        messageText += ` | Stop Price: ${formatNumber(parseFloat(logData.stop_price))}`;
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

// 모달 관련 함수
export function showConfirmModal(text) {
    confirmModalText.innerHTML = text;
    confirmModal.style.display = "block";
}

export function hideConfirmModal() {
    confirmModal.style.display = "none";
}

export function showAlertModal(text) {
    alertModalText.textContent = text;
    alertModal.style.display = "block";
}

export function hideAlertModal() {
    alertModal.style.display = "none";
}

export function hideDetailsModal() {
    modal.style.display = "none";
}
