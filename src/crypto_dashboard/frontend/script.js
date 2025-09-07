document.addEventListener("DOMContentLoaded", () => {
    // 기본적인 HTML 이스케이핑 함수
    function basicEscape(text) {
        if (typeof text !== 'string') return text;
        return text
            .replace(/&/g, '&')
            .replace(/</g, '<')
            .replace(/>/g, '>');
    }

    const cryptoContainer = document.getElementById("crypto-container");
    const totalValueElement = document.getElementById("total-value");
    const ordersContainer = document.getElementById("orders-container");
    const logsContainer = document.getElementById("logs-container");
    const referenceTimeContainer = document.getElementById("reference-time-container");
    const referenceTimeElement = document.getElementById("reference-time");
    const exchangeTabsContainer = document.getElementById("exchange-tabs");

    let currentPrices = {};
    let cachedOrders = [];
    let cachedLogs = [];
    let exchanges = [];
    let activeExchange = '';
    let followCoins = {};  // follow 코인 캐시: {exchange: Set(coins)}
    let valueFormats = {};  // value 소수점 포맷: {exchange: integer}
    let exchangeInfo = {}; // quote_currency 등 거래소 정보 저장
    let referencePrices = {}; // 기준 가격 정보 저장

    const modal = document.getElementById("details-modal");
    const closeButton = document.querySelector(".close-button");

    const confirmModal = document.getElementById("confirm-modal");
    const confirmModalText = document.getElementById("confirm-modal-text");
    const confirmYesBtn = document.getElementById("confirm-yes-btn");
    const confirmNoBtn = document.getElementById("confirm-no-btn");
    const alertModal = document.getElementById("alert-modal");
    const alertModalText = document.getElementById("alert-modal-text");
    const alertOkBtn = document.getElementById("alert-ok-btn");

    let websocket;
    let reconnectTimeout;
    let pendingNlpCommand = null;

    function connect() {
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
                if (data.type === 'exchanges_list') {
                    exchanges = data.data;
                    createExchangeTabs();
                    if (exchanges.length > 0) {
                        setActiveExchange(exchanges[0]);
                    }
                } else if (data.type === 'follow_coins') {
                    const { exchange, follows } = data;
                    followCoins[exchange] = new Set(follows);
                    console.log(`Received follow coins for ${exchange}:`, follows);
                } else if (data.type === 'value_format') {
                    const { exchange, value_decimal_places, quote_currency } = data;
                    valueFormats[exchange] = value_decimal_places;
                    if (!exchangeInfo[exchange]) exchangeInfo[exchange] = {};
                    exchangeInfo[exchange].quoteCurrency = quote_currency;
                    console.log(`Received config for ${exchange}:`, { value_decimal_places, quote_currency });

                    // Hardcode the price of the quote currency to 1
                    if (quote_currency) {
                        const marketSymbol = `${quote_currency}/${quote_currency}`;
                        currentPrices[marketSymbol] = 1.0;
                    }
                } else if (data.type === 'portfolio_update') {
                    const { symbol, exchange, free, locked, avg_buy_price, realised_pnl } = data; // symbol is "BTC"
                    const quoteCurrency = exchangeInfo[exchange]?.quoteCurrency;
                    if (!quoteCurrency) return;

                    const marketSymbol = `${symbol}/${quoteCurrency}`;
                    const uniqueId = `${exchange}_${marketSymbol}`;
                    const card = document.getElementById(uniqueId);

                    // Combine portfolio data with the latest price to calculate derived values
                    const price = currentPrices[marketSymbol] || (card ? parseFloat(card.dataset.price) : 0);
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
                } else if (data.type === 'remove_holding') {
                    const quoteCurrency = exchangeInfo[data.exchange]?.quoteCurrency;
                    if (!quoteCurrency) return;
                    const marketSymbol = `${data.symbol}/${quoteCurrency}`;
                    const uniqueId = `${data.exchange}_${marketSymbol}`;
                    const card = document.getElementById(uniqueId);
                    if (card) {
                        card.remove();
                        updateTotalValue();
                    }
                } else if (data.type === 'orders_update') {
                    cachedOrders = data.data;
                    updateOrdersList();
                } else if (data.type === 'price_update') {
                    currentPrices[data.symbol] = parseFloat(data.price);
                    updatePriceDiffs(); // Update orders list with new price diff

                    // Also trigger a re-render for the main crypto card
                    const uniqueId = `${data.exchange}_${data.symbol}`;
                    const card = document.getElementById(uniqueId);
                    if (card) {
                        const free = parseFloat(card.dataset.free || 0);
                        const locked = parseFloat(card.dataset.locked || 0);
                        const value = data.price * (free + locked);

                        const renderData = {
                            ...card.dataset, // Preserve all existing data
                            symbol: data.symbol,
                            price: data.price,
                            value: value,
                        };
                        renderCryptoCard(renderData);
                    }
                } else if (data.type === 'log') {
                    cachedLogs.unshift(data);
                    // Only prepend the new log if it belongs to the active exchange
                    if (data.exchange === activeExchange) {
                        const logElement = createLogElement(data);
                        if (logElement) {
                            logsContainer.prepend(logElement);
                        }
                    }
                } else if (data.type === 'reference_price_info') {
                    updateReferencePriceInfo(data.time);
                    referencePrices = data.prices || {};
                    // Re-render all visible cards to apply the new reference prices
                    document.querySelectorAll('#crypto-container .crypto-card').forEach(card => {
                        if (card.style.display !== 'none') {
                            // Re-rendering needs a complete data object.
                            // We reconstruct it from the card's dataset.
                            const marketSymbol = card.dataset.symbol;
                            const exchange = card.dataset.exchange;
                            const price = currentPrices[marketSymbol] || parseFloat(card.dataset.price || 0);
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
                } else if (data.type === 'nlp_trade_confirm') {
                    confirmModalText.innerHTML = formatTradeCommandForConfirmation(data.command);
                    pendingNlpCommand = data.command;
                    confirmModal.style.display = "block";
                } else if (data.type === 'nlp_error') {
                    alertModalText.textContent = data.message;
                    alertModal.style.display = "block";
                } 
            } catch (e) {
                console.error("Failed to parse JSON:", e);
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
                reconnectTimeout = setTimeout(connect, 3000);
            }
        };
    }

    connect();

    closeButton.onclick = () => {
        modal.style.display = "none";
    }
    window.onclick = (event) => {
        if (event.target == modal) {
            modal.style.display = "none";
        }
    }

    cryptoContainer.addEventListener('click', (event) => {
        const card = event.target.closest('.crypto-card');
        if (card && card.dataset.symbol) {
            openDetailsModal(card.dataset);
        }
    });

    const cancelSelectedBtn = document.getElementById("cancel-selected-btn");
    const cancelAllBtn = document.getElementById("cancel-all-btn");

    function checkSocketAndSend(payload) {
        if (websocket && websocket.readyState === WebSocket.OPEN) {
            websocket.send(JSON.stringify(payload));
            return true;
        } else {
            alertModalText.textContent = "WebSocket is not connected. Please wait.";
            alertModal.style.display = "block";
            console.error("WebSocket is not open. State:", websocket ? websocket.readyState : 'null');
            return false;
        }
    }

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
            alertModalText.textContent = "취소할 주문을 선택하세요.";
            alertModal.style.display = "block";
        }
    });

    alertOkBtn.addEventListener("click", () => {
        alertModal.style.display = "none";
    });

    cancelAllBtn.addEventListener("click", () => {
        confirmModalText.textContent = "모든 주문을 취소하시겠습니까?";
        confirmModal.style.display = "block";
    });

    confirmYesBtn.addEventListener("click", () => {
        let payload;
        if (pendingNlpCommand) {
            payload = { type: 'nlp_execute', command: pendingNlpCommand, exchange: activeExchange };
            pendingNlpCommand = null;
        } else {
            payload = { type: 'cancel_all_orders', exchange: activeExchange };
        }
        checkSocketAndSend(payload);
        confirmModal.style.display = "none";
    });

    confirmNoBtn.addEventListener("click", () => {
        pendingNlpCommand = null;
        confirmModal.style.display = "none";
    });

    ordersContainer.addEventListener('click', (event) => {
        const card = event.target.closest('.crypto-card');
        if (!card) return;

        const checkbox = card.querySelector('.order-checkbox');
        if (checkbox && event.target.tagName.toLowerCase() !== 'input') {
            checkbox.checked = !checkbox.checked;
        }
        
        if(checkbox) {
            card.classList.toggle('selected', checkbox.checked);
        }
    });

    const commandBar = document.getElementById('command-bar');
    const commandInput = document.getElementById('command-input');

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

    if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', () => {
            const viewport = window.visualViewport;
            const keyboardHeight = window.innerHeight - viewport.height;
            commandBar.style.bottom = `${keyboardHeight}px`;
        });
    }

    function createExchangeTabs() {
        exchangeTabsContainer.innerHTML = '';
        exchanges.forEach(exchange => {
            const tab = document.createElement('button');
            tab.className = 'exchange-tab-button';
            tab.textContent = exchange;
            tab.dataset.exchange = exchange;
            tab.onclick = () => setActiveExchange(exchange);
            exchangeTabsContainer.appendChild(tab);
        });
    }

    function setActiveExchange(exchangeName) {
        activeExchange = exchangeName;
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

    // Renders or updates the crypto card DOM based on a complete data object
    function renderCryptoCard(data) {
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
        } else {
            if (avgPriceElement) avgPriceElement.textContent = '-';
            if (roiElement) {
                roiElement.className = 'info-value roi-value';
                roiElement.textContent = '-';
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

    let totalValue = 0;

    function updateTotalValue() {
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

    function updatePriceDiffs() {
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

    function updateOrdersList() {
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

    function updateLogsList() {
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

    function formatNumber(num) {
        if (typeof num !== 'number' || !isFinite(num)) return num;
        let numStr = num.toFixed(8);
        if (numStr.includes('.')) {
            numStr = numStr.replace(/0+$/, '');
            numStr = numStr.replace(/\.$/, '');
        }
        return numStr;
    }

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

    function openDetailsModal(dataset) {
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

    function updateReferencePriceInfo(time) {
        if (time) {
            const date = new Date(time);
            referenceTimeElement.textContent = date.toLocaleString();
        } else {
            referenceTimeContainer.style.display = 'none';
        }
    }

    function formatTradeCommandForConfirmation(command) {
        const intentKr = command.intent === 'buy' ? '매수' : '매도';
        const orderTypeKr = command.order_type === 'market' ? '시장가' : '지정가';
        const coinSymbol = command.symbol && command.symbol.includes('/') ? command.symbol.split('/')[0] : (command.symbol || 'Unknown');

        let htmlParts = [
            '<div class="trade-confirmation">',
            '<h3>주문 확인</h3>',
            '<div class="confirmation-details">',
            `<div class="detail-row"><span class="detail-label">종류:</span><span class="detail-value intent-${command.intent}">${intentKr}</span></div>`,
            `<div class="detail-row"><span class="detail-label">코인:</span><span class="detail-value">${coinSymbol}</span></div>`,
            `<div class="detail-row"><span class="detail-label">주문 유형:</span><span class="detail-value">${orderTypeKr}</span></div>`,
        ];

        if (command.amount) {
            htmlParts.push(`<div class="detail-row"><span class="detail-label">수량:</span><span class="detail-value">${command.amount}</span></div>`);
        }

        if (command.price) {
            htmlParts.push(`<div class="detail-row"><span class="detail-label">지정가:</span><span class="detail-value">${command.price}</span></div>`);
        }

        if (command.stop_price) {
            htmlParts.push(`<div class="detail-row"><span class="detail-label">스탑 가격:</span><span class="detail-value">${command.stop_price}</span></div>`);
        }

        if (command.total_cost) {
            htmlParts.push(`<div class="detail-row"><span class="detail-label">총 주문액:</span><span class="detail-value">${command.total_cost}</span></div>`);
        }

        htmlParts.push(
            '</div>',
            '<div class="confirmation-notice">',
            '<p>이 주문을 정말로 실행하시겠습니까?</p>',
            '</div>',
            '</div>'
        );

        return htmlParts.join('');
    }
});

function openTab(evt, tabName) {
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

document.addEventListener("DOMContentLoaded", () => {
    document.querySelector(".tab-button").click();
});