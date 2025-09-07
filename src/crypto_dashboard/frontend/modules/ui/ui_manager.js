// src/crypto_dashboard/frontend/modules/ui/ui_manager.js

import { renderCryptoCard, updateTotalValue } from './crypto_card.js';
import { updateOrdersList } from './order_manager.js';
import { updateLogsList } from './log_manager.js';
// import { openDetailsModal, updateReferencePriceInfo, showConfirmModal, hideConfirmModal, showAlertModal, hideAlertModal, hideDetailsModal } from './modal_manager.js';
import { openDetailsModal, showConfirmModal, hideConfirmModal, showAlertModal, hideAlertModal, hideDetailsModal } from './modal_manager.js';
import { createExchangeTabs, setActiveExchange, openTab } from './tab_manager.js';

// Re-export for backward compatibility and central access
export {
    renderCryptoCard,
    updateTotalValue,
    updateOrdersList,
    updateLogsList,
    openDetailsModal,
    // updateReferencePriceInfo,  // reference price 제거됨
    createExchangeTabs,
    setActiveExchange,
    openTab,
    showConfirmModal,
    hideConfirmModal,
    showAlertModal,
    hideAlertModal,
    hideDetailsModal
};
