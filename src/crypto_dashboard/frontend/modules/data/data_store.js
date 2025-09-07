// src/crypto_dashboard/frontend/modules/data/data_store.js

export let currentPrices = {};
export let cachedOrders = [];
export let cachedLogs = [];
export let exchanges = [];
export let activeExchange = '';
export let followCoins = {};  // follow 코인 캐시: {exchange: Set(coins)}
export let valueFormats = {};  // value 소수점 포맷: {exchange: integer}
export let exchangeInfo = {}; // quote_currency 등 거래소 정보 저장
export let referencePrices = {}; // 기준 가격 정보 저장
export let pendingNlpCommand = null;

export function getExchangeInfo() {
    return exchangeInfo;
}

export function getCurrentPrices() {
    return currentPrices;
}

// 데이터 업데이트 함수 (필요에 따라 setter 함수 추가 가능)
export function updateCurrentPrices(newPrices) {
    currentPrices = { ...currentPrices, ...newPrices };
}

export function updateCachedOrders(newOrders) {
    cachedOrders = newOrders;
}

export function addCachedLog(log) {
    cachedLogs.unshift(log);
}

export function updateExchanges(newExchanges) {
    exchanges = newExchanges;
}

export function setActiveExchange(exchange) {
    activeExchange = exchange;
}

export function updateFollowCoins(exchange, follows) {
    followCoins[exchange] = new Set(follows);
}

export function updateValueFormats(exchange, value_decimal_places) {
    valueFormats[exchange] = value_decimal_places;
}

export function updateExchangeInfo(exchange, quote_currency) {
    if (!exchangeInfo[exchange]) exchangeInfo[exchange] = {};
    exchangeInfo[exchange].quoteCurrency = quote_currency;
}

export function updateReferencePrices(time, prices) {
    // referenceTimeElement 업데이트는 UI 모듈에서 처리
    referencePrices = prices || {};
}

export function setPendingNlpCommand(command) {
    pendingNlpCommand = command;
}

export function clearPendingNlpCommand() {
    pendingNlpCommand = null;
}
