document.addEventListener("DOMContentLoaded", () => {
    const cryptoContainer = document.getElementById("crypto-container");
    const websocket = new WebSocket(`ws://${window.location.host}/ws`); // 백엔드 웹소켓 서버 주소
    const totalValueElement = document.getElementById("total-value");
    let currentPrices = {}; // To store current market prices
    let cachedOrders = []; // To store the last known list of open orders

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

    websocket.onmessage = (event) => {
        console.log("Received data:", event.data);
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'remove_holding') {
                const card = document.getElementById(data.symbol);
                if (card) {
                    card.remove();
                    updateTotalValue();
                }
            } else if (data.type === 'orders_update') {
                updateOrdersList(data.data);
            } else {
                updateCryptoCard(data);
            }
        } catch (e) {
            console.error("Failed to parse JSON:", e);
        }
    };

    websocket.onopen = () => {
        console.log("WebSocket connection established");
    };

    websocket.onerror = (error) => {
        console.error("WebSocket error:", error);
        // 추가적인 에러 정보 로깅
        console.log("WebSocket error object:", JSON.stringify(error, ["message", "name", "type"]));
    };

    function updateCryptoCard(data) {
        const { symbol, price, amount, value } = data;
        currentPrices[symbol] = parseFloat(price); // Update current price
        updateOrdersList(cachedOrders); // Redraw orders list with new current price
        let card = document.getElementById(symbol);

        if (!card) {
            card = document.createElement("div");
            card.id = symbol;
            card.className = "crypto-card";
            cryptoContainer.appendChild(card);
        }

        card.innerHTML = `
            <h2>${symbol}</h2>
            <p class="amount">Amount: ${amount}</p>
            <p class="price">Price: $${parseFloat(price).toFixed(2)}</p>
            <p class="value" data-value="${value}">Value: $${parseFloat(value).toFixed(2)}</p>
        `;

        updateTotalValue();
    }

    function updateTotalValue() {
        const totalValueElement = document.getElementById("total-value");
        let totalValue = 0;
        document.querySelectorAll('#crypto-container .crypto-card .value').forEach(valueElement => {
            totalValue += parseFloat(valueElement.dataset.value || 0);
        });
        totalValueElement.textContent = `$${totalValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }

    function updateOrdersList(orders) {
        cachedOrders = orders; // Cache the latest orders list
        const ordersContainer = document.getElementById("orders-container");
        ordersContainer.innerHTML = ""; // Clear previous list

        if (orders.length === 0) {
            ordersContainer.innerHTML = "<p>현재 활성화된 주문이 없습니다.</p>";
            return;
        }

        orders.sort((a, b) => b.timestamp - a.timestamp).forEach(order => {
            const orderCard = document.createElement("div");
            orderCard.className = "crypto-card"; // Reuse crypto-card style
            const baseSymbol = order.symbol.replace('USDT', '').replace('/', '');
            const sideClass = order.side.toLowerCase() === 'buy' ? 'side-buy' : 'side-sell';
            const orderDate = new Date(order.timestamp).toLocaleString();
            
            let priceDiffHtml = '';
            const currentPrice = currentPrices[baseSymbol];
            if (currentPrice) {
                const priceDiff = ((order.price - currentPrice) / currentPrice) * 100;
                const diffClass = priceDiff >= 0 ? 'side-buy' : 'side-sell';
                priceDiffHtml = `<p class="price-diff ${diffClass}">Diff: ${priceDiff.toFixed(2)}%</p>`;
            }

            orderCard.innerHTML = `
                <div style="display: flex; align-items: center;">
                    <h2 style="flex-grow: 1; text-align: center; margin-left: 24px;">${baseSymbol}</h2>
                    <input type="checkbox" class="order-checkbox" data-order-id="${order.id}" data-symbol="${order.symbol}" style="margin-left: auto;">
                </div>
                <p class="side ${sideClass}">${order.side}</p>
                <p>Price: ${parseFloat(order.price).toFixed(2)}</p>
                ${priceDiffHtml}
                <p>Amount: ${order.amount}</p>
                <p class="value">Value: $${parseFloat(order.value).toFixed(2)}</p>
                <p class="date">${orderDate}</p>
            `;
            ordersContainer.appendChild(orderCard);
        });
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