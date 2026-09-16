// Tailscale <-> Proton VPN Bridge - Web UI
// Live status, traffic path, drag-and-drop uploads, and setup wizard controls.

(function () {
  "use strict";

  var previousTraffic = null;
  var lastAnnouncedConnection = null;
  var statusRequestInFlight = false;

  function setText(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function numberValue(value) {
    var parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
  }

  function formatBytes(value) {
    if (value < 1024) return Math.round(value) + " B/s";
    if (value < 1024 * 1024) return (value / 1024).toFixed(1) + " KB/s";
    if (value < 1024 * 1024 * 1024) return (value / (1024 * 1024)).toFixed(1) + " MB/s";
    return (value / (1024 * 1024 * 1024)).toFixed(1) + " GB/s";
  }

  function formatPackets(value) {
    return new Intl.NumberFormat().format(Math.round(value)) + " packets";
  }

  function resetTraffic() {
    ["tailnet-rx-rate", "vpn-tx-rate", "vpn-rx-rate", "tailnet-tx-rate"].forEach(function (id) {
      setText(id, "--");
    });
    ["tailnet-rx-packets", "vpn-tx-packets", "vpn-rx-packets", "tailnet-tx-packets"].forEach(function (id) {
      setText(id, "-- packets");
    });
  }

  function updateTraffic(data, panel) {
    var now = Date.now();
    var sampleTime = Date.parse(data.traffic_updated || data.last_updated || "");
    if (!Number.isFinite(sampleTime)) sampleTime = now;
    var hasNewSample = !previousTraffic || sampleTime > previousTraffic.sampleTime;
    var current = {
      tailscale_rx_bytes: numberValue(data.tailscale_rx_bytes),
      tailscale_tx_bytes: numberValue(data.tailscale_tx_bytes),
      tailscale_rx_packets: numberValue(data.tailscale_rx_packets),
      tailscale_tx_packets: numberValue(data.tailscale_tx_packets),
      vpn_rx_bytes: numberValue(data.vpn_rx_bytes),
      vpn_tx_bytes: numberValue(data.vpn_tx_bytes),
      vpn_rx_packets: numberValue(data.vpn_rx_packets),
      vpn_tx_packets: numberValue(data.vpn_tx_packets)
    };
    var elapsed = previousTraffic && hasNewSample
      ? Math.max((sampleTime - previousTraffic.sampleTime) / 1000, 0.25)
      : 1;

    function delta(key) {
      if (!previousTraffic || !hasNewSample || current[key] < previousTraffic.values[key]) return 0;
      return current[key] - previousTraffic.values[key];
    }

    setText("tailnet-rx-rate", formatBytes(delta("tailscale_rx_bytes") / elapsed));
    setText("tailnet-rx-packets", formatPackets(current.tailscale_rx_packets));
    setText("vpn-tx-rate", formatBytes(delta("vpn_tx_bytes") / elapsed));
    setText("vpn-tx-packets", formatPackets(current.vpn_tx_packets));
    setText("vpn-rx-rate", formatBytes(delta("vpn_rx_bytes") / elapsed));
    setText("vpn-rx-packets", formatPackets(current.vpn_rx_packets));
    setText("tailnet-tx-rate", formatBytes(delta("tailscale_tx_bytes") / elapsed));
    setText("tailnet-tx-packets", formatPackets(current.tailscale_tx_packets));

    panel.classList.remove("flow-ingress", "flow-egress", "flow-return");
    if (previousTraffic && hasNewSample && data.connected) {
      if (delta("tailscale_rx_packets") > 0) panel.classList.add("flow-ingress");
      if (delta("vpn_tx_packets") > 0) panel.classList.add("flow-egress");
      if (delta("vpn_rx_packets") > 0 || delta("tailscale_tx_packets") > 0) panel.classList.add("flow-return");
    }
    previousTraffic = { sampleTime: sampleTime, values: current };
  }

  function applyStatus(data) {
    var connected = !!data.connected;
    var panel = document.getElementById("schematic-panel");
    if (panel) panel.classList.toggle("connected", connected);
    if (panel) panel.classList.remove("stale");

    var stateEl = document.getElementById("tb-state");
    var protoEl = document.getElementById("tb-proto");
    if (stateEl) {
      stateEl.textContent = connected ? "CONNECTED" : (data.auth_url ? "PENDING" : "DISCONNECTED");
      stateEl.className = connected ? "state-ok" : (data.auth_url ? "state-pending" : "state-off");
    }
    if (protoEl) protoEl.textContent = (data.vpn_mode || "-").toUpperCase();

    var connectionLabel = connected ? "Connected" : (data.auth_url ? "Waiting" : "Disconnected");
    setText("traffic-live-label", connectionLabel);
    if (connectionLabel !== lastAnnouncedConnection) {
      setText("connection-announcement", "Connection status: " + connectionLabel);
      lastAnnouncedConnection = connectionLabel;
    }

    var nodeIp = connected && data.tailscale_ip ? data.tailscale_ip : "not connected";
    setText("node-ip", nodeIp);
    setText("node-ip-mobile", nodeIp);

    setText("status-detail-text",
      data.last_updated ? ("Last checked: " + data.last_updated) : "No status data yet.");

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
    if (panel) updateTraffic(data, panel);
  }

  function refreshStatus() {
    var el = document.getElementById("schematic-panel");
    if (!el) return;
    if (statusRequestInFlight) return;
    statusRequestInFlight = true;
    fetch(el.dataset.statusUrl, { credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("status request failed");
        return res.json();
      })
      .then(applyStatus)
      .catch(function () {
        el.classList.remove("connected", "flow-ingress", "flow-egress", "flow-return");
        previousTraffic = null;
        setText("tb-state", "UNAVAILABLE");
        setText("traffic-live-label", "Unavailable");
        setText("node-ip", "not connected");
        setText("node-ip-mobile", "not connected");
        setText("status-detail-text", "Unable to read bridge status.");
        setText("connection-announcement", "Connection status: unavailable");
        lastAnnouncedConnection = "Unavailable";
        resetTraffic();
        var stateEl = document.getElementById("tb-state");
        if (stateEl) stateEl.className = "state-off";
        el.classList.add("stale");
      })
      .then(function () {
        statusRequestInFlight = false;
      });
  }

  function initStatusPolling() {
    if (!document.getElementById("schematic-panel")) return;
    refreshStatus();
    setInterval(refreshStatus, 2000);
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
          label.textContent = "Selected: ";
          var filename = document.createElement("span");
          filename.className = "filename";
          filename.textContent = input.files[0].name;
          label.appendChild(filename);
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

    // Switch between a Tailscale auth key and a login link.
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
