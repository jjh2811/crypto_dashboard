document.addEventListener("DOMContentLoaded", async () => {
    const { connectWebSocket } = await import('./websocket/websocket.js');
    connectWebSocket();

    const { initializeEventListeners } = await import('./event/event_handlers.js');
    initializeEventListeners();

    const initialTabButton = document.querySelector(".tab-button.active");
    if (initialTabButton) {
        const { openTab } = await import('./ui/ui_manager.js');
        openTab({ currentTarget: initialTabButton }, initialTabButton.dataset.tabName);
    }
});
