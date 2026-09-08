// Tailscale <-> Proton VPN Bridge - Web UI
// Estado em tempo real, diagrama do percurso do sinal, uploads por
// arrastar-e-largar, e navegação do assistente de configuração.

(function () {
  "use strict";

  function setText(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function applyStatus(data) {
    var connected = !!data.connected;
    var panel = document.getElementById("schematic-panel");
    if (panel) panel.classList.toggle("connected", connected);

    var stateEl = document.getElementById("tb-state");
    var protoEl = document.getElementById("tb-proto");
    if (stateEl) {
      stateEl.textContent = connected ? "LIGADO" : (data.auth_url ? "PENDENTE" : "DESLIGADO");
      stateEl.className = connected ? "state-ok" : (data.auth_url ? "state-pending" : "state-off");
    }
    if (protoEl) protoEl.textContent = (data.vpn_mode || "-").toUpperCase();

    setText("node-ip", connected && data.tailscale_ip ? data.tailscale_ip : "sem ligação");

    setText("status-detail-text",
      data.last_updated ? ("Última verificação: " + data.last_updated) : "Sem dados de estado ainda.");

    var strip = document.getElementById("action-strip");
    var link = document.getElementById("action-strip-link");
    if (strip && link) {
      if (!connected && data.auth_url) {
        link.href = data.auth_url;
        strip.classList.add("show");
      } else {
        strip.classList.remove("show");
      }
    }
  }

  function refreshStatus() {
    var el = document.getElementById("schematic-panel");
    if (!el) return;
    fetch(el.dataset.statusUrl, { credentials: "same-origin" })
      .then(function (res) { return res.json(); })
      .then(applyStatus)
      .catch(function () {
        setText("tb-state", "INDISPONÍVEL");
      });
  }

  function initStatusPolling() {
    if (!document.getElementById("schematic-panel")) return;
    refreshStatus();
    setInterval(refreshStatus, 5000);
  }

  function initVpnTypeToggle() {
    var radios = document.querySelectorAll('input[name="vpn_type"]');
    if (!radios.length) return;
    function apply() {
      var selected = document.querySelector('input[name="vpn_type"]:checked').value;
      document.querySelectorAll(".vpn-block").forEach(function (block) {
        var show = selected === "auto" || block.dataset.vpn === selected;
        block.classList.toggle("active", show);
      });
    }
    radios.forEach(function (r) { r.addEventListener("change", apply); });
    apply();
  }

  function initDropzones() {
    document.querySelectorAll(".dropzone").forEach(function (zone) {
      var input = zone.querySelector('input[type="file"]');
      var label = zone.querySelector(".dz-label");
      if (!input) return;

      function announce() {
        if (input.files && input.files.length) {
          label.innerHTML = 'Selecionado: <span class="filename">' + input.files[0].name + "</span>";
        }
      }

      zone.addEventListener("click", function () { input.click(); });
      input.addEventListener("change", announce);

      ["dragenter", "dragover"].forEach(function (evt) {
        zone.addEventListener(evt, function (e) {
          e.preventDefault();
          zone.classList.add("dragover");
        });
      });
      ["dragleave", "drop"].forEach(function (evt) {
        zone.addEventListener(evt, function (e) {
          e.preventDefault();
          zone.classList.remove("dragover");
        });
      });
      zone.addEventListener("drop", function (e) {
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
          input.files = e.dataTransfer.files;
          announce();
        }
      });
    });
  }

  function initMethodCards() {
    document.querySelectorAll(".method-card").forEach(function (card) {
      var input = card.querySelector("input");
      if (!input) return;
      function sync() {
        var group = card.closest("fieldset") || document;
        group.querySelectorAll(".method-card").forEach(function (c) { c.classList.remove("selected"); });
        if (input.checked) card.classList.add("selected");
      }
      card.addEventListener("click", function (e) {
        if (e.target !== input) input.checked = true;
        sync();
        input.dispatchEvent(new Event("change"));
      });
      input.addEventListener("change", sync);
      sync();
    });
  }

  function initWizard() {
    var wizard = document.getElementById("wizard");
    if (!wizard) return;

    var steps = Array.prototype.slice.call(wizard.querySelectorAll(".step-card"));
    var segs = Array.prototype.slice.call(wizard.querySelectorAll(".wizard-rail .seg"));
    var current = 0;

    function show(index) {
      steps.forEach(function (s, i) { s.classList.toggle("active", i === index); });
      segs.forEach(function (s, i) { s.classList.toggle("done", i <= index); });
      current = index;
      wizard.scrollIntoView({ block: "start", behavior: "smooth" });
    }

    wizard.querySelectorAll("[data-next]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (btn.dataset.validate) {
          var target = wizard.querySelector(btn.dataset.validate);
          if (target && !target.checkValidity()) {
            target.reportValidity();
            return;
          }
        }
        show(Math.min(current + 1, steps.length - 1));
      });
    });
    wizard.querySelectorAll("[data-prev]").forEach(function (btn) {
      btn.addEventListener("click", function () { show(Math.max(current - 1, 0)); });
    });

    // Alterna entre "auth key" e "link de login" para o Tailscale
    var methodRadios = wizard.querySelectorAll('input[name="ts_method"]');
    var authKeyBlock = wizard.querySelector('[data-ts-block="authkey"]');
    var authKeyInput = authKeyBlock ? authKeyBlock.querySelector('input[name="ts_authkey"]') : null;
    function syncMethod() {
      var val = wizard.querySelector('input[name="ts_method"]:checked');
      var isAuthKey = !!(val && val.value === "authkey");
      if (authKeyBlock) authKeyBlock.classList.toggle("active", isAuthKey);
      if (authKeyInput) authKeyInput.required = isAuthKey;
    }
    methodRadios.forEach(function (r) { r.addEventListener("change", syncMethod); });
    syncMethod();

    show(0);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initStatusPolling();
    initVpnTypeToggle();
    initDropzones();
    initMethodCards();
    initWizard();
  });
})();
