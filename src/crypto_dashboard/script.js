document.addEventListener("DOMContentLoaded", () => {
    const cryptoContainer = document.getElementById("crypto-container");
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const websocket = new WebSocket(`${protocol}//${window.location.host}/ws`);
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

    const modal = document.getElementById("details-modal");
    const closeButton = document.querySelector(".close-button");

    const confirmModal = document.getElementById("confirm-modal");
    const confirmModalText = document.getElementById("confirm-modal-text");
    const confirmYesBtn = document.getElementById("confirm-yes-btn");
    const confirmNoBtn = document.getElementById("confirm-no-btn");
    const alertModal = document.getElementById("alert-modal");
    const alertModalText = document.getElementById("alert-modal-text");
    const alertOkBtn = document.getElementById("alert-ok-btn");

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

    cancelSelectedBtn.addEventListener("click", () => {
        const selectedOrders = [];
        document.querySelectorAll(".order-checkbox:checked").forEach(checkbox => {
            selectedOrders.push({
                id: checkbox.dataset.orderId,
                symbol: checkbox.dataset.symbol
            });
        });
        if (selectedOrders.length > 0) {
            websocket.send(JSON.stringify({ type: 'cancel_orders', orders: selectedOrders, exchange: activeExchange }));
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
        if (pendingNlpCommand) {
            websocket.send(JSON.stringify({ type: 'nlp_execute', command: pendingNlpCommand, exchange: activeExchange }));
            pendingNlpCommand = null;
        } else {
            websocket.send(JSON.stringify({ type: 'cancel_all_orders', exchange: activeExchange }));
        }
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
    let pendingNlpCommand = null;

    commandInput.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            const text = commandInput.value.trim();
            if (text) {
                websocket.send(JSON.stringify({ type: 'nlp_command', text: text, exchange: activeExchange }));
                commandInput.value = '';
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

    websocket.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'exchanges_list') {
                exchanges = data.data;
                createExchangeTabs();
                if (exchanges.length > 0) {
                    setActiveExchange(exchanges[0]);
                }
            } else if (data.type === 'balance_update') {
                updateCryptoCard(data);
            } else if (data.type === 'remove_holding') {
                const card = document.getElementById(data.symbol);
                if (card) {
                    card.remove();
                    updateTotalValue();
                }
            } else if (data.type === 'orders_update') {
                cachedOrders = data.data;
                updateOrdersList();
            } else if (data.type === 'log') {
                cachedLogs.unshift(data);
                updateLogsList();
            } else if (data.type === 'reference_price_info') {
                updateReferencePriceInfo(data.time);
            } else if (data.type === 'reference_price_info') {
                updateReferencePriceInfo(data.time);
            } else if (data.type === 'nlp_trade_confirm') {
                confirmModalText.innerHTML = data.confirmation_message;
                pendingNlpCommand = data.command;
                confirmModal.style.display = "block";
            } else if (data.type === 'nlp_error') {
                alertModalText.textContent = data.message;
                alertModal.style.display = "block";
            } else {
                currentPrices[data.symbol] = parseFloat(data.price);
                updateModalUnrealisedPnL(data.symbol, data.price);
                updatePriceDiffs();
            }
        } catch (e) {
            console.error("Failed to parse JSON:", e);
        }
    };

    websocket.onopen = () => console.log("WebSocket connection established");
    websocket.onerror = (error) => {
        console.error("WebSocket error:", error);
    };
    websocket.onclose = (event) => {
        console.log("WebSocket connection closed:", event.code, event.reason);
        if (event.code === 1008) {
            alert("세션이 만료되었거나 인증에 실패했습니다. 다시 로그인해주세요.");
            window.location.href = "/login";
        } else {
            console.error("WebSocket closed with code:", event.code, "reason:", event.reason);
        }
    };

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

    function updateCryptoCard(data) {
        const { symbol, price, exchange } = data;
        currentPrices[symbol] = parseFloat(price);
        let card = document.getElementById(symbol);

        if (!card) {
            card = document.createElement("div");
            card.id = symbol;
            card.className = "crypto-card";
            cryptoContainer.appendChild(card);
        }
        
        Object.keys(data).forEach(key => {
            card.dataset[key] = data[key];
        });

        card.innerHTML = createCryptoCardHTML(data);
        if (activeExchange && card.dataset.exchange !== activeExchange) {
            card.style.display = 'none';
        }

        updateTotalValue();

        if (modal.style.display === "block" && document.getElementById("modal-crypto-name").textContent === symbol) {
            openDetailsModal(card.dataset);
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
        totalValueElement.textContent = `${totalValue.toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 })}`;
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

            const baseSymbol = order.symbol.replace('USDT', '').replace('/', '');
            const currentPrice = currentPrices[baseSymbol];
            const priceDiffElement = card.querySelector('.price-diff-value');

            if (currentPrice && priceDiffElement) {
                const priceDiff = currentPrice > 0 ? ((order.price - currentPrice) / currentPrice) * 100 : 0;
                
                let diffClass;
                if (order.side.toLowerCase() === 'buy') {
                    diffClass = priceDiff > 0 ? 'side-buy' : 'side-sell';
                } else { // SELL
                    diffClass = priceDiff < 0 ? 'side-buy' : 'side-sell';
                }

                priceDiffElement.className = `info-value price-diff-value ${diffClass}`;
                priceDiffElement.textContent = `${priceDiff.toFixed(2)}%`;
            }
        });
    }

    function updateOrdersList() {
        const checkedOrderIds = new Set();
        ordersContainer.querySelectorAll('.order-checkbox:checked').forEach(checkbox => {
            checkedOrderIds.add(checkbox.dataset.orderId);
        });

        ordersContainer.innerHTML = "";
        
        const filteredOrders = cachedOrders.filter(order => order.exchange === activeExchange);

        if (filteredOrders.length === 0) {
            ordersContainer.innerHTML = "<p>현재 활성화된 주문이 없습니다.</p>";
            return;
        }

        filteredOrders.sort((a, b) => b.timestamp - a.timestamp).forEach(order => {
            const orderCard = document.createElement("div");
            orderCard.className = "crypto-card";
            orderCard.dataset.orderId = order.id;
            orderCard.dataset.exchange = order.exchange;
            const baseSymbol = order.symbol.replace('USDT', '').replace('/', '');
            const currentPrice = currentPrices[baseSymbol];
            
            orderCard.innerHTML = createOrderCardHTML(order, currentPrice);
            ordersContainer.appendChild(orderCard);
        });

        checkedOrderIds.forEach(orderId => {
            const checkbox = ordersContainer.querySelector(`.order-checkbox[data-order-id='${orderId}']`);
            if (checkbox) {
                checkbox.checked = true;
                const card = checkbox.closest('.crypto-card');
                if (card) {
                    card.classList.add('selected');
                }
            }
        });
    }

    function updateLogsList() {
        logsContainer.innerHTML = "";
        const filteredLogs = cachedLogs.filter(log => log.exchange === activeExchange);

        filteredLogs.forEach(data => {
            const logData = data.message;

            // Skip success logs from NLP trade execution
            if (logData.status === 'success') {
                return;
            }

            const logElement = document.createElement('p');
            const now = new Date(data.timestamp);
            const timestamp = `${now.getMonth() + 1}/${now.getDate()} ${now.toLocaleTimeString()}`;

            let messageText = `[${timestamp}]`;
            messageText += ` ${logData.status}`;

            // Add order_id early if available for better visibility
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
                messageText += ` (${logData.side})`
            }
            if (logData.price) {
                messageText += ` | Price: ${parseFloat(logData.price.toPrecision(8))}`;
            }
            if (logData.amount) {
                messageText += ` | Amount: ${parseFloat(logData.amount.toPrecision(8))}`;
            }
            if (logData.reason) {
                messageText += ` | Reason: ${logData.reason}`;
            }

            logElement.textContent = messageText;
            logsContainer.appendChild(logElement);
        });
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
        const baseSymbol = order.symbol.replace('USDT', '').replace('/', '');
        const side = (order.side || '').toUpperCase();
        const sideClass = side === 'BUY' ? 'side-buy' : 'side-sell';
        const orderDate = new Date(order.timestamp).toLocaleString();
        
        const quoteCurrency = order.quote_currency || 'USDT';

        let priceDiffText = '-';
        let diffClass = '';
        if (currentPrice) {
            const priceDiff = currentPrice > 0 ? ((order.price - currentPrice) / currentPrice) * 100 : 0;
            
            if (order.side.toLowerCase() === 'buy') {
                diffClass = priceDiff > 0 ? 'side-buy' : 'side-sell';
            } else { // SELL
                diffClass = priceDiff < 0 ? 'side-buy' : 'side-sell';
            }
            priceDiffText = `${priceDiff.toFixed(2)}%`;
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

        return `
            <div style="display: flex; align-items: center; justify-content: space-between;">
                <h2 style="margin: 0; flex-grow: 1; text-align: center;">${baseSymbol}</h2>
                <input type="checkbox" class="order-checkbox" data-order-id="${order.id}" data-symbol="${order.symbol}">
            </div>
            <div class="info-row">
                <span class="info-label">Side:</span>
                <span class="info-value ${sideClass}">${side}</span>
            </div>
            <div class="info-row">
                <span class="info-label">Price:</span>
                <span class="info-value">${formatNumber(order.price)}</span>
            </div>
            <div class="info-row">
                <span class="info-label">Market:</span>
                <span class="info-value">${quoteCurrency}</span>
            </div>
            <div class="info-row">
                <span class="info-label">Diff:</span>
                <span class="info-value price-diff-value ${diffClass}">${priceDiffText}</span>
            </div>
            <div class="info-row">
                <span class="info-label">Amount:</span>
                <span class="info-value">${amountText}</span>
            </div>
            <div class="progress-bar-container">
                <div class="progress-bar" style="width: ${progress}%;"></div>
            </div>
            <div class="info-row">
                <span class="info-label">Value:</span>
                <span class="info-value">${unfilledValue.toFixed(3)}</span>
            </div>
            <div class="info-row">
                <span class="info-label">Date:</span>
                <span class="info-value">${orderDate}</span>
            </div>
        `;
    }

    function createCryptoCardHTML(data) {
        const symbol = data.symbol || 'Unknown';
        const price = Number.isFinite(parseFloat(data.price)) ? parseFloat(data.price) : 0;
        const free = Number.isFinite(parseFloat(data.free)) ? parseFloat(data.free) : 0;
        const locked = Number.isFinite(parseFloat(data.locked)) ? parseFloat(data.locked) : 0;
        const value = Number.isFinite(parseFloat(data.value)) ? parseFloat(data.value) : 0;
        
        const totalAmount = free + locked;
        const lockedAmountHtml = locked > 1e-8 ? `<p class="locked">Locked: ${parseFloat(locked.toFixed(8))}</p>` : '';
        
        let avgBuyPriceHtml;
        if (data.avg_buy_price && Number.isFinite(parseFloat(data.avg_buy_price))) {
            const avg_buy_price = parseFloat(data.avg_buy_price);
            const price = Number.isFinite(parseFloat(data.price)) ? parseFloat(data.price) : 0;
            const profitPercent = avg_buy_price > 0 ? ((price - avg_buy_price) / avg_buy_price) * 100 : 0;
            const profitClass = profitPercent >= 0 ? 'profit-positive' : 'profit-negative';
            avgBuyPriceHtml = `
                <div class="info-row">
                    <span class="info-label">Avg. Price:</span>
                    <span class="info-value">${parseFloat(avg_buy_price.toPrecision(8))}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">P/L:</span>
                    <span class="info-value ${profitClass}">${profitPercent.toFixed(2)}%</span>
                </div>
            `;
        } else {
            avgBuyPriceHtml = `
                <div class="info-row">
                    <span class="info-label">Avg. Price:</span>
                    <span class="info-value">-</span>
                </div>
                <div class="info-row">
                    <span class="info-label">P/L:</span>
                    <span class="info-value">-</span>
                </div>
            `;
        }

        let priceChangeSpan = '';
        if (data.price_change_percent !== undefined) {
            const change = parseFloat(data.price_change_percent);
            const changeClass = change >= 0 ? 'profit-positive' : 'profit-negative';
            priceChangeSpan = ` <span class="${changeClass}">(${change.toFixed(2)}%)</span>`;
        }

        return `
            <h2>${symbol}</h2>
            <div class="info-row">
                <span class="info-label">Price:</span>
                <span class="info-value">${parseFloat(price.toPrecision(8))}${priceChangeSpan}</span>
            </div>
            ${avgBuyPriceHtml}
            <div class="info-row value" data-value="${value.toFixed(3)}">
                <span class="info-label">Value:</span>
                <span class="info-value">${value.toFixed(3)}</span>
            </div>
            <div class="info-row share">
                <span class="info-label">Share:</span>
                <span class="info-value">-</span>
            </div>
        `;
    }

    function openDetailsModal(dataset) {
        document.getElementById("modal-crypto-name").textContent = dataset.symbol;
        const free = parseFloat(dataset.free || 0);
        const locked = parseFloat(dataset.locked || 0);
        const total = free + locked;
        const percentage = total > 0 ? ((free / total) * 100).toFixed(2) : 0;
        const realised_pnl = parseFloat(dataset.realised_pnl);
        const unrealised_pnl = parseFloat(dataset.unrealised_pnl);

        const balanceDetailsContainer = document.getElementById("modal-crypto-balance-details");
        
        const formatPnl = (pnl) => {
            if (isNaN(pnl)) {
                return '<span class="info-value profit-neutral">-</span>';
            }
            const pnlClass = pnl >= 0 ? 'profit-positive' : 'profit-negative';
            const pnlSign = pnl > 0 ? '+' : '';
            return `<span class="info-value ${pnlClass}">${pnlSign}${pnl.toFixed(3)}</span>`;
        };

        balanceDetailsContainer.innerHTML = `
            <div class="info-row">
                <span class="info-label">Free:</span>
                <span class="info-value">${parseFloat(free.toFixed(8))} / ${parseFloat(total.toFixed(8))} (${percentage}%)</span>
            </div>
            <div class="info-row">
                <span class="info-label">Unrealised PnL:</span>
                ${formatPnl(unrealised_pnl)}
            </div>
            <div class="info-row">
                <span class="info-label">Realised PnL:</span>
                ${formatPnl(realised_pnl)}
            </div>
        `;
        
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

    function updateModalUnrealisedPnL(symbol, price) {
        if (modal.style.display === "block" && document.getElementById("modal-crypto-name").textContent === symbol) {
            const card = document.getElementById(symbol);
            if (card) {
                const avg_buy_price = parseFloat(card.dataset.avg_buy_price);
                const free = parseFloat(card.dataset.free || 0);
                const locked = parseFloat(card.dataset.locked || 0);
                const totalAmount = free + locked;
                if (avg_buy_price && totalAmount) {
                    const unrealised_pnl = (price - avg_buy_price) * totalAmount;
                    const pnlElement = modal.querySelector("#modal-crypto-balance-details .info-row:nth-child(2) .info-value");
                    if (pnlElement) {
                        pnlElement.textContent = (unrealised_pnl > 0 ? '+' : '') + unrealised_pnl.toFixed(3);
                        pnlElement.className = `info-value ${unrealised_pnl >= 0 ? 'profit-positive' : 'profit-negative'}`;
                    }
                }
            }
        }
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

// Set default tab
document.addEventListener("DOMContentLoaded", () => {
    document.querySelector(".tab-button").click();
});
