document.addEventListener("DOMContentLoaded", () => {
    const cryptoContainer = document.getElementById("crypto-container");
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const websocket = new WebSocket(`${protocol}//${window.location.host}/ws`);
    const totalValueElement = document.getElementById("total-value");
    const ordersContainer = document.getElementById("orders-container");
    let currentPrices = {};
    let cachedOrders = [];
    const modal = document.getElementById("details-modal");
    const closeButton = document.querySelector(".close-button");

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
            websocket.send(JSON.stringify({ type: 'cancel_orders', orders: selectedOrders }));
        } else {
            alert("취소할 주문을 선택하세요.");
        }
    });

    cancelAllBtn.addEventListener("click", () => {
        if (confirm("모든 주문을 취소하시겠습니까?")) {
            websocket.send(JSON.stringify({ type: 'cancel_all_orders' }));
        }
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

    websocket.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'remove_holding') {
                const card = document.getElementById(data.symbol);
                if (card) {
                    card.remove();
                    updateTotalValue();
                }
            } else if (data.type === 'orders_update') {
                cachedOrders = data.data;
                updateOrdersList(cachedOrders); // Full redraw only on order changes
            } else {
                // 'free' 또는 'amount' 키가 존재하면 보유 자산 정보로 간주하고 카드 업데이트
                if (data.free !== undefined || data.amount !== undefined) {
                    updateCryptoCard(data);
                } else {
                    // 그렇지 않으면 가격 정보로 간주
                    currentPrices[data.symbol] = parseFloat(data.price);
                }
                updatePriceDiffs(); // Update only price diffs on price changes
            }
        } catch (e) {
            console.error("Failed to parse JSON:", e);
        }
    };

    websocket.onopen = () => console.log("WebSocket connection established");
    websocket.onerror = (error) => console.error("WebSocket error:", error);

    function updateCryptoCard(data) {
        const { symbol, price } = data;
        currentPrices[symbol] = parseFloat(price);
        let card = document.getElementById(symbol);

        if (!card) {
            card = document.createElement("div");
            card.id = symbol;
            card.className = "crypto-card";
            cryptoContainer.appendChild(card);
        }
        
        // Store data in dataset for modal
        Object.keys(data).forEach(key => {
            card.dataset[key] = data[key];
        });

        card.innerHTML = createCryptoCardHTML(data);
        updateTotalValue();
    }

    function updateTotalValue() {
        let totalValue = 0;
        document.querySelectorAll('#crypto-container .crypto-card .value').forEach(el => {
            totalValue += parseFloat(el.dataset.value || 0);
        });
        totalValueElement.textContent = `$${totalValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }

    function updatePriceDiffs() {
        ordersContainer.querySelectorAll('.crypto-card[data-order-id]').forEach(card => {
            const orderId = card.dataset.orderId;
            const order = cachedOrders.find(o => o.id.toString() === orderId);
            if (!order) return;

            const baseSymbol = order.symbol.replace('USDT', '').replace('/', '');
            const currentPrice = currentPrices[baseSymbol];
            const priceDiffElement = card.querySelector('.price-diff');

            if (currentPrice && priceDiffElement) {
                const priceDiff = ((order.price - currentPrice) / currentPrice) * 100;
                const diffClass = priceDiff >= 0 ? 'side-buy' : 'side-sell';
                priceDiffElement.className = `price-diff ${diffClass}`;
                priceDiffElement.textContent = `Diff: ${priceDiff.toFixed(2)}%`;
            }
        });
    }

    function updateOrdersList(orders) {
        const checkedOrderIds = new Set();
        ordersContainer.querySelectorAll('.order-checkbox:checked').forEach(checkbox => {
            checkedOrderIds.add(checkbox.dataset.orderId);
        });

        ordersContainer.innerHTML = ""; 

        if (orders.length === 0) {
            ordersContainer.innerHTML = "<p>현재 활성화된 주문이 없습니다.</p>";
            return;
        }

        orders.sort((a, b) => b.timestamp - a.timestamp).forEach(order => {
            const orderCard = document.createElement("div");
            orderCard.className = "crypto-card";
            orderCard.dataset.orderId = order.id; // Set dataset for identification
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

    function createCryptoCardHTML(data) {
        const symbol = data.symbol || 'Unknown';
        // 모든 숫자 값을 파싱하기 전에 유효성을 확인하고, 아니면 0으로 설정합니다.
        const price = Number.isFinite(parseFloat(data.price)) ? parseFloat(data.price) : 0;
        const free = Number.isFinite(parseFloat(data.free)) ? parseFloat(data.free) : 0;
        const locked = Number.isFinite(parseFloat(data.locked)) ? parseFloat(data.locked) : 0;
        const value = Number.isFinite(parseFloat(data.value)) ? parseFloat(data.value) : 0;
        
        const totalAmount = free + locked;
        // toFixed(8)로 정밀도를 유지한 뒤 parseFloat으로 불필요한 0을 제거합니다.
        const lockedAmountHtml = locked > 1e-8 ? `<p class="locked">Locked: ${parseFloat(locked.toFixed(8))}</p>` : '';
        
        let avgBuyPriceHtml = '<p class="avg-buy-price">-</p>';
        if (data.avg_buy_price) {
            const avg_buy_price = parseFloat(data.avg_buy_price);
            const profitPercent = ((price - avg_buy_price) / avg_buy_price) * 100;
            const profitClass = profitPercent >= 0 ? 'profit-positive' : 'profit-negative';
            avgBuyPriceHtml = `
                <div class="info-row">
                    <span class="info-label">Avg. Price:</span>
                    <span class="info-value">$${parseFloat(avg_buy_price.toPrecision(8))}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">P/L:</span>
                    <span class="info-value ${profitClass}">${profitPercent.toFixed(2)}%</span>
                </div>
            `;
        }

        return `
            <h2>${symbol}</h2>
            <div class="info-row">
                <span class="info-label">Price:</span>
                <span class="info-value">$${parseFloat(price.toPrecision(8))}</span>
            </div>
            ${avgBuyPriceHtml}
            <div class="info-row value" data-value="${value.toFixed(2)}">
                <span class="info-label">Value:</span>
                <span class="info-value">$${value.toFixed(2)}</span>
            </div>
        `;
    }

    function createOrderCardHTML(order, currentPrice) {
        const baseSymbol = order.symbol.replace('USDT', '').replace('/', '');
        const sideClass = order.side.toLowerCase() === 'buy' ? 'side-buy' : 'side-sell';
        const orderDate = new Date(order.timestamp).toLocaleString();
        
        let priceDiffHtml = '<p class="price-diff">-</p>';
        if (currentPrice) {
            const priceDiff = ((order.price - currentPrice) / currentPrice) * 100;
            const diffClass = priceDiff >= 0 ? 'side-buy' : 'side-sell';
            priceDiffHtml = `<p class="price-diff ${diffClass}">Diff: ${priceDiff.toFixed(2)}%</p>`;
        }

        return `
            <div style="display: flex; align-items: center; justify-content: space-between;">
                <h2 style="margin: 0;">${baseSymbol}</h2>
                <input type="checkbox" class="order-checkbox" data-order-id="${order.id}" data-symbol="${order.symbol}">
            </div>
            <p class="side ${sideClass}">${order.side}</p>
            <p class="price">Price: ${parseFloat(order.price)}</p>
            ${priceDiffHtml}
            <p class="amount">Amount: ${order.amount}</p>
            <p class="value">Value: $${parseFloat(order.value).toFixed(2)}</p>
            <p class="date">${orderDate}</p>
        `;
    }

    function openDetailsModal(dataset) {
        document.getElementById("modal-crypto-name").textContent = dataset.symbol;
        const free = parseFloat(dataset.free || 0);
        const locked = parseFloat(dataset.locked || 0);
        const total = free + locked;

        document.getElementById("modal-crypto-free").textContent = `Free: ${parseFloat(free.toFixed(8))}`;
        document.getElementById("modal-crypto-locked").textContent = `Locked: ${parseFloat(locked.toFixed(8))}`;
        document.getElementById("modal-crypto-total").textContent = `Total: ${parseFloat(total.toFixed(8))}`;
        
        modal.style.display = "block";
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