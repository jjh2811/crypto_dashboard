document.addEventListener("DOMContentLoaded", () => {
    const cryptoContainer = document.getElementById("crypto-container");

    // TODO: 백엔드에서 초기 잔고 데이터를 가져와서 화면에 표시해야 합니다.
    // 이 부분은 백엔드와 연동하면서 구현합니다.

    const websocket = new WebSocket(`ws://${window.location.host}/ws`); // 백엔드 웹소켓 서버 주소

    websocket.onmessage = (event) => {
        console.log("Received data:", event.data);
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'remove') {
                const card = document.getElementById(data.symbol);
                if (card) {
                    card.remove();
                }
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
            <p class="value">Value: $${parseFloat(value).toFixed(2)}</p>
        `;
    }
});