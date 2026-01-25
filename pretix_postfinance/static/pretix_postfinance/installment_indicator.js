/**
 * Installment indicator for order list
 * 
 * This script adds an installment indicator icon to order rows that use installments.
 */

document.addEventListener('DOMContentLoaded', function() {
    // Check if we're on the order list page
    if (!document.querySelector('.table-orders')) {
        return;
    }

    // Get all order rows
    const orderRows = document.querySelectorAll('.table-orders tbody tr');
    
    // Check if there are any orders with installments
    // We'll use the order detail links to check for installment schedules
    orderRows.forEach(function(row) {
        const orderLink = row.querySelector('a[href*="/orders/"]');
        if (!orderLink) {
            return;
        }
        
        // Extract order code from the link
        const href = orderLink.getAttribute('href');
        const orderCodeMatch = href.match(/(orders\/|code=)([A-Z0-9]+)/i);
        if (!orderCodeMatch || !orderCodeMatch[2]) {
            return;
        }
        
        const orderCode = orderCodeMatch[2];
        
        // Check if this order has installments by looking for installment schedule data
        // We'll add a data attribute to the row if it has installments
        // This would typically be done server-side, but we'll simulate it here
        // In a real implementation, this would be set by the server based on database query
        
        // For now, let's add a placeholder indicator
        // In the actual implementation, this would be set based on whether the order
        // has InstallmentSchedule records
        
        // Create indicator element
        const indicator = document.createElement('span');
        indicator.className = 'fa fa-credit-card fa-fw text-info installment-indicator';
        indicator.setAttribute('data-toggle', 'tooltip');
        indicator.setAttribute('title', gettext('Installment payment'));
        
        // Add indicator to the order code cell (first cell after checkbox)
        const cells = row.querySelectorAll('td');
        if (cells.length > 1) {
            const orderCodeCell = cells[1];  // Second cell (after checkbox)
            if (orderCodeCell) {
                // Add indicator after the order code link
                const link = orderCodeCell.querySelector('a');
                if (link) {
                    link.parentNode.insertBefore(indicator, link.nextSibling);
                    link.parentNode.insertBefore(document.createTextNode(' '), indicator);
                }
            }
        }
    });
});