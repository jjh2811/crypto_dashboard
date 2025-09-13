// src/crypto_dashboard/frontend/modules/ui/crypto_card.js

import { formatNumber } from '../utils/utils.js';
import { activeExchange, currentPrices, currentPercentages, priceTrackedCoins, valueFormats, referencePrices, getExchangeInfo, getCurrentPercentages, cachedOrders } from '../data/data_store.js';

// DOM 요소 캐싱
const cryptoContainer = document.getElementById("crypto-container");
const totalValueElement = document.getElementById("total-value");

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

    // 24시간 변화율 계산
    const percentages = getCurrentPercentages();
    const percentageChange = percentages[symbol] || 0;
    let percentageClass = '';
    let percentageText = '0.00%';

    if (percentageChange !== 0) {
        percentageText = `${percentageChange >= 0 ? '+' : ''}${percentageChange.toFixed(2)}%`;
        percentageClass = percentageChange >= 0 ? 'profit-positive' : 'profit-negative';
    }

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
            <span class="info-label">24h Change:</span>
            <span class="info-value">
                <span class="price-change-24h ${percentageClass}">${percentageText}</span>
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
    const { symbol, price, exchange, value, avg_buy_price, free, locked } = data;
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
    const priceChange24hElement = card.querySelector(".price-change-24h");
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

    // Update 24h Change Percentage
    if (priceChange24hElement) {
        const percentages = getCurrentPercentages();
        const percentageChange = percentages[symbol] || 0;
        let percentageClass = '';
        let percentageText = '0.00%';

        if (percentageChange !== 0) {
            percentageText = `${percentageChange >= 0 ? '+' : ''}${percentageChange.toFixed(2)}%`;
            percentageClass = percentageChange >= 0 ? 'profit-positive' : 'profit-negative';
        }

        priceChange24hElement.className = `price-change-24h ${percentageClass}`;
        priceChange24hElement.textContent = percentageText;
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
        const unrealised_pnl = parseFloat(data.unrealised_pnl || 0);
    card.dataset.unrealised_pnl = unrealised_pnl;
    }


    // Update styling for followed zero-balance coins
    const totalAmount = parseFloat(card.dataset.free || 0) + parseFloat(card.dataset.locked || 0);
    const isZeroBalance = totalAmount === 0;
    const baseAsset = symbol.split('/')[0];
    const is_follow = priceTrackedCoins[exchange]?.has(baseAsset) || false;
    card.classList.toggle('follow-zero-balance', is_follow && isZeroBalance);

    // Hide if not in active exchange
    if (activeExchange && card.dataset.exchange !== activeExchange) {
        card.style.display = 'none';
    }

    updateTotalValue();
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
function updatePriceDiffs() {
    document.querySelectorAll('#orders-container .crypto-card[data-order-id]').forEach(card => {
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