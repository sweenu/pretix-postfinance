'use strict';

$(function() {
    var btn = document.getElementById('postfinance-test-connection');
    if (!btn) return;

    var result = document.getElementById('postfinance-test-result');
    var testUrl = btn.getAttribute('data-test-url');

    btn.addEventListener('click', function() {
        btn.disabled = true;
        btn.textContent = gettext('Testing...');
        result.textContent = '';

        var csrfToken = document.querySelector('input[name=csrfmiddlewaretoken]');
        fetch(testUrl, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken ? csrfToken.value : '',
                'Content-Type': 'application/json'
            },
            credentials: 'same-origin'
        })
        .then(function(response) { return response.json(); })
        .then(function(data) {
            btn.disabled = false;
            btn.textContent = gettext('Test Connection');
            result.textContent = data.message;
            result.style.color = data.success ? 'green' : 'red';
        })
        .catch(function(error) {
            btn.disabled = false;
            btn.textContent = gettext('Test Connection');
            result.textContent = gettext('Connection test failed. Please try again.');
            result.style.color = 'red';
            console.error('PostFinance test connection error:', error);
        });
    });
});
