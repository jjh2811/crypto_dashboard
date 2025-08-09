document.addEventListener("DOMContentLoaded", () => {
    const cryptoContainer = document.getElementById("crypto-container");

    // TODO: 백엔드에서 초기 잔고 데이터를 가져와서 화면에 표시해야 합니다.
    // 이 부분은 백엔드와 연동하면서 구현합니다.

    const websocket = new WebSocket("ws://localhost:8080/ws"); // 백엔드 웹소켓 서버 주소

    websocket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        updateCryptoCard(data);
    };

    websocket.onopen = () => {
        console.log("WebSocket connection established");
    };

    websocket.onerror = (error) => {
        console.error("WebSocket error:", error);
    };

    function updateCryptoCard(data) {
        const { symbol, price, amount } = data;
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
            <p class="price">$${parseFloat(price).toFixed(2)}</p>
        `;
    }
});