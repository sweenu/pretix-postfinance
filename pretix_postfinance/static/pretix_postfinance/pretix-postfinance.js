"use strict";

$(function () {
  // Test Connection button
  var testBtn = document.getElementById("postfinance-test-connection");
  if (testBtn) {
    var testResult = document.getElementById("postfinance-test-result");
    var testUrl = testBtn.getAttribute("data-test-url");

    testBtn.addEventListener("click", function () {
      testBtn.disabled = true;
      testBtn.textContent = gettext("Testing...");
      testResult.textContent = "";

      var csrfToken = document.querySelector("input[name=csrfmiddlewaretoken]");
      fetch(testUrl, {
        method: "POST",
        headers: {
          "X-CSRFToken": csrfToken ? csrfToken.value : "",
          "Content-Type": "application/json",
        },
        credentials: "same-origin",
      })
        .then(function (response) {
          return response.json();
        })
        .then(function (data) {
          testBtn.disabled = false;
          testBtn.textContent = gettext("Test Connection");
          testResult.textContent = data.message;
          testResult.style.color = data.success ? "green" : "red";
        })
        .catch(function (error) {
          testBtn.disabled = false;
          testBtn.textContent = gettext("Test Connection");
          testResult.textContent = gettext(
            "Connection test failed. Please try again.",
          );
          testResult.style.color = "red";
          console.error("PostFinance test connection error:", error);
        });
    });
  }

  // Setup Webhooks button
  var setupBtn = document.getElementById("postfinance-setup-webhooks");
  if (setupBtn) {
    var setupResult = document.getElementById("postfinance-setup-result");
    var setupUrl = setupBtn.getAttribute("data-setup-url");

    setupBtn.addEventListener("click", function () {
      setupBtn.disabled = true;
      setupBtn.textContent = gettext("Setting up...");
      setupResult.textContent = "";

      var csrfToken = document.querySelector("input[name=csrfmiddlewaretoken]");
      fetch(setupUrl, {
        method: "POST",
        headers: {
          "X-CSRFToken": csrfToken ? csrfToken.value : "",
          "Content-Type": "application/json",
        },
        credentials: "same-origin",
      })
        .then(function (response) {
          return response.json();
        })
        .then(function (data) {
          setupBtn.disabled = false;
          setupBtn.textContent = gettext("Setup Webhooks Automatically");
          setupResult.textContent = data.message;
          setupResult.style.color = data.success ? "green" : "red";
        })
        .catch(function (error) {
          setupBtn.disabled = false;
          setupBtn.textContent = gettext("Setup Webhooks Automatically");
          setupResult.textContent = gettext(
            "Webhook setup failed. Please try again.",
          );
          setupResult.style.color = "red";
          console.error("PostFinance setup webhooks error:", error);
        });
    });
  }
});
