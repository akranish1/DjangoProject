/* ==========================================================================
   EventHub - vanilla JS progressive enhancement.
   Every interaction has a server-rendered fallback; nothing is simulated.
   ========================================================================== */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const csrfToken = () => {
    const input = $("input[name=csrfmiddlewaretoken]");
    if (input) return input.value;
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  };
  const debounce = (fn, wait = 320) => {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), wait); };
  };

  async function request(url, { method = "GET", body = null, json = true } = {}) {
    const options = {
      method,
      headers: { "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": csrfToken() },
      credentials: "same-origin",
    };
    if (body instanceof FormData) options.body = body;
    else if (body) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
    const response = await fetch(url, options);
    let data = null;
    try { data = json ? await response.json() : await response.text(); } catch (e) { data = null; }
    if (!response.ok) {
      const message = (data && (data.message || (data.errors && JSON.stringify(data.errors)))) || `Request failed (${response.status})`;
      throw new Error(message);
    }
    return data;
  }

  /* ---------------------------------------------------------------- toasts */
  function toast(message, level = "info", title = "") {
    let host = $(".messages");
    if (!host) { host = document.createElement("div"); host.className = "messages"; host.setAttribute("role", "status"); host.setAttribute("aria-live", "polite"); document.body.appendChild(host); }
    const icons = { success: "i-check", error: "i-alert", warning: "i-alert", info: "i-info" };
    const el = document.createElement("div");
    el.className = `alert alert-${level}`;
    el.innerHTML = `
      <svg aria-hidden="true"><use href="#${icons[level] || "i-info"}"></use></svg>
      <div class="alert-body">${title ? `<div class="alert-title">${title}</div>` : ""}<div>${message}</div></div>
      <button type="button" class="alert-close" aria-label="Dismiss notification">
        <svg aria-hidden="true" style="width:16px;height:16px"><use href="#i-x"></use></svg>
      </button>`;
    host.appendChild(el);
    const close = () => { el.style.opacity = "0"; el.style.transform = "translateY(-8px)"; setTimeout(() => el.remove(), 200); };
    $(".alert-close", el).addEventListener("click", close);
    setTimeout(close, 6500);
  }
  window.EventHubToast = toast;

  function initDismissibleAlerts() {
    $$(".messages .alert").forEach((el) => {
      const btn = $(".alert-close", el);
      if (btn) btn.addEventListener("click", () => el.remove());
      setTimeout(() => { if (el.parentNode) el.remove(); }, 7000);
    });
  }

  /* ------------------------------------------------------- sidebar / menus */
  function initSidebar() {
    const toggle = $("[data-sidebar-toggle]");
    const sidebar = $(".sidebar");
    const scrim = $(".scrim");
    if (!toggle || !sidebar) return;
    const setOpen = (open) => {
      sidebar.classList.toggle("is-open", open);
      if (scrim) scrim.classList.toggle("is-visible", open);
      toggle.setAttribute("aria-expanded", String(open));
    };
    toggle.addEventListener("click", () => setOpen(!sidebar.classList.contains("is-open")));
    if (scrim) scrim.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
  }

  function initDropdowns() {
    const dropdowns = $$("[data-dropdown]");
    dropdowns.forEach((dd) => {
      const trigger = $("[data-dropdown-trigger]", dd);
      if (!trigger) return;
      trigger.addEventListener("click", (e) => {
        e.preventDefault();
        const open = dd.classList.contains("is-open");
        dropdowns.forEach((other) => other.classList.remove("is-open"));
        dd.classList.toggle("is-open", !open);
        trigger.setAttribute("aria-expanded", String(!open));
      });
    });
    document.addEventListener("click", (e) => {
      if (!e.target.closest("[data-dropdown]")) dropdowns.forEach((dd) => dd.classList.remove("is-open"));
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") dropdowns.forEach((dd) => dd.classList.remove("is-open"));
    });
  }

  /* -------------------------------------------------------- confirm dialogs */
  function initConfirmations() {
    document.addEventListener("submit", (e) => {
      const form = e.target;
      if (!form.matches("[data-confirm]") || form.dataset.confirmed === "yes") return;
      e.preventDefault();
      const message = form.dataset.confirm || "Are you sure?";
      const dialog = document.createElement("dialog");
      dialog.className = "modal";
      dialog.innerHTML = `
        <div class="modal-head">
          <div><h3 style="margin:0">Please confirm</h3><div class="small muted">This action is recorded on the platform activity log.</div></div>
        </div>
        <div class="modal-body"><div class="note"><svg aria-hidden="true"><use href="#i-alert"></use></svg><span>${message}</span></div></div>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" data-cancel>Cancel</button>
          <button type="button" class="btn btn-danger" data-confirm-ok>Yes, continue</button>
        </div>`;
      document.body.appendChild(dialog);
      dialog.showModal();
      $("[data-cancel]", dialog).addEventListener("click", () => { dialog.close(); dialog.remove(); });
      $("[data-confirm-ok]", dialog).addEventListener("click", () => {
        dialog.close(); dialog.remove();
        form.dataset.confirmed = "yes";
        form.submit();
      });
      dialog.addEventListener("close", () => dialog.remove());
    });
  }

  /* --------------------------------------------------------- async filters */
  function initAsyncFilters() {
    const region = $("[data-async-region]");
    if (!region || !region.dataset.asyncUrl) return;
    const form = $("[data-filter-form]");
    const load = async (url, { push = true } = {}) => {
      region.classList.add("is-loading");
      try {
        const html = await request(url, { json: false });
        region.innerHTML = html;
        if (push) history.replaceState({}, "", url);
      } catch (err) {
        toast(err.message, "error", "Could not refresh results");
      } finally {
        region.classList.remove("is-loading");
      }
    };
    window.EventHubLoadRegion = load;

    if (form) {
      const run = debounce(() => {
        const params = new URLSearchParams(new FormData(form));
        if (form.dataset.resetPage !== "off") params.delete("page");
        const qs = params.toString();
        load(`${region.dataset.asyncUrl}${qs ? "?" + qs : ""}`);
      }, 380);
      form.addEventListener("input", (e) => { if (e.target.type === "search" || e.target.type === "text") run(); });
      form.addEventListener("change", run);
      form.addEventListener("submit", (e) => { e.preventDefault(); run(); });
      const reset = $("[data-filter-reset]", form.parentElement || document);
      if (reset) reset.addEventListener("click", () => { form.reset(); run(); });
    }
    region.addEventListener("click", (e) => {
      const link = e.target.closest("[data-page-link]");
      if (!link) return;
      e.preventDefault();
      load(link.href);
    });
  }

  /* ------------------------------------------------------ notifications UI */
  function initNotifications() {
    const panel = $("[data-notification-panel]");
    if (!panel) return;
    const openUrl = panel.dataset.openUrl;
    panel.addEventListener("click", async (e) => {
      const item = e.target.closest("[data-notification-id]");
      if (!item) return;
      const id = item.dataset.notificationId;
      if (e.target.closest("a")) return;
      e.preventDefault();
      try {
        const data = await request(`${openUrl}${id}/read/`, { method: "POST" });
        item.classList.remove("is-unread");
        const dot = $("[data-unread-dot]", item);
        if (dot) dot.remove();
        updateBellCount(data.unread);
        if (item.dataset.link) window.location.href = item.dataset.link;
      } catch (err) { toast(err.message, "error", "Notification"); }
    });

    const markAll = $("[data-mark-all-read]");
    if (markAll) {
      markAll.addEventListener("click", async (e) => {
        e.preventDefault();
        try {
          const data = await request(markAll.dataset.url, { method: "POST", json: false });
          $$(".notif-item.is-unread").forEach((el) => { el.classList.remove("is-unread"); const d = $("[data-unread-dot]", el); if (d) d.remove(); });
          updateBellCount(0);
          toast("All notifications marked as read.", "success");
          if (markAll.dataset.reload === "yes") window.location.reload();
        } catch (err) { toast(err.message, "error", "Notifications"); }
      });
    }
  }

  function updateBellCount(count) {
    $$("[data-bell-count]").forEach((el) => {
      if (count > 0) { el.textContent = count > 99 ? "99+" : String(count); el.hidden = false; }
      else { el.hidden = true; }
    });
  }

  /* ------------------------------------------------- attendance & check-in */
  function initCheckIn() {
    const form = $("[data-check-in-form]");
    if (!form) return;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const button = $("button[type=submit]", form);
      const original = button.innerHTML;
      button.disabled = true;
      button.innerHTML = '<span class="spinner"></span> Checking in…';
      try {
        const data = await request(form.action, { method: "POST", body: new FormData(form) });
        toast(data.message, "success", "Checked in");
        const row = $(`[data-registration-row="${data.ticket_code}"]`);
        if (row) {
          const cell = $("[data-attendance-cell]", row);
          if (cell) cell.innerHTML = `<span class="badge badge-success">Present · ${data.checked_in_at}</span>`;
        }
        form.reset();
        $("input[name=ticket_code]", form).focus();
      } catch (err) {
        toast(err.message, "error", "Check-in failed");
      } finally {
        button.disabled = false;
        button.innerHTML = original;
      }
    });
  }

  function initStatusSelects() {
    $$("[data-status-form]").forEach((form) => {
      const select = $("select", form);
      if (!select) return;
      select.addEventListener("change", async () => {
        form.classList.add("is-loading");
        const body = new FormData(form);
        try {
          const data = await request(form.action, { method: "POST", body });
          const badge = $(`[data-status-badge="${form.dataset.registration}"]`);
          if (badge) { badge.textContent = data.label; badge.className = `badge badge-${data.tone}`; }
          toast(`${data.label} saved.`, "success");
        } catch (err) {
          toast(err.message, "error", "Could not update status");
          select.value = form.dataset.originalStatus || select.value;
        } finally {
          form.classList.remove("is-loading");
        }
      });
    });
  }

  /* --------------------------------------------------------- dashboard charts */
  function initCharts() {
    $$("[data-chart]").forEach(async (host) => {
      const url = host.dataset.chartUrl;
      if (!url) return;
      try {
        const data = await request(url);
        if (data.labels && data.series && data.series.length) drawMultiLine(host, data);
        else if (data.registrations) drawBars(host, data.registrations);
      } catch (err) {
        host.innerHTML = `<div class="empty" style="padding:26px"><p class="small">${err.message}</p></div>`;
      }
    });
  }

  function svgEl(name, attrs) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v));
    return el;
  }

  function drawBars(host, series) {
    const width = 720, height = 230, pad = { t: 18, r: 14, b: 34, l: 34 };
    const max = Math.max(1, ...series.values);
    const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Registrations over time" });
    const innerW = width - pad.l - pad.r, innerH = height - pad.t - pad.b;
    const barW = innerW / series.values.length;
    series.values.forEach((value, i) => {
      const h = (value / max) * innerH;
      const bar = svgEl("rect", {
        class: "chart-bar", x: pad.l + i * barW + barW * 0.18, y: pad.t + innerH - h,
        width: barW * 0.64, height: Math.max(h, 2), rx: 5, fill: "url(#barGrad)",
      });
      bar.appendChild(svgEl("title", {})).textContent = `${series.labels[i]}: ${value}`;
      svg.appendChild(bar);
      if (i % Math.ceil(series.values.length / 7) === 0) {
        const label = svgEl("text", { x: pad.l + i * barW + barW / 2, y: height - 12, "text-anchor": "middle", "font-size": "10", fill: "#8b96ab" });
        label.textContent = series.labels[i];
        svg.appendChild(label);
      }
    });
    const defs = svgEl("defs", {});
    const grad = svgEl("linearGradient", { id: "barGrad", x1: "0", y1: "0", x2: "0", y2: "1" });
    grad.appendChild(svgEl("stop", { offset: "0%", "stop-color": "#6366f1" }));
    grad.appendChild(svgEl("stop", { offset: "100%", "stop-color": "#22d3ee" }));
    defs.appendChild(grad);
    svg.insertBefore(defs, svg.firstChild);
    svg.appendChild(svgEl("line", { x1: pad.l, y1: pad.t + innerH, x2: width - pad.r, y2: pad.t + innerH, stroke: "#e6e9f0" }));
    host.innerHTML = "";
    host.appendChild(svg);
  }

  function drawMultiLine(host, data) {
    const width = 720, height = 230, pad = { t: 18, r: 14, b: 34, l: 34 };
    const all = data.series.flatMap((s) => s.values);
    const max = Math.max(1, ...all);
    const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Platform growth" });
    const innerW = width - pad.l - pad.r, innerH = height - pad.t - pad.b;
    data.series.forEach((series) => {
      const step = series.values.length > 1 ? innerW / (series.values.length - 1) : innerW;
      const points = series.values.map((v, i) => [pad.l + i * step, pad.t + innerH - (v / max) * innerH]);
      const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
      svg.appendChild(svgEl("path", { d: `${path} L${points[points.length - 1][0]},${pad.t + innerH} L${points[0][0]},${pad.t + innerH} Z`, fill: series.color, opacity: "0.09" }));
      svg.appendChild(svgEl("path", { d: path, fill: "none", stroke: series.color, "stroke-width": "2.4", "stroke-linejoin": "round", "stroke-linecap": "round" }));
      points.forEach((p, i) => {
        const dot = svgEl("circle", { cx: p[0], cy: p[1], r: 3.4, fill: "#fff", stroke: series.color, "stroke-width": 2 });
        dot.appendChild(svgEl("title", {})).textContent = `${series.name} · ${data.labels[i]}: ${series.values[i]}`;
        svg.appendChild(dot);
      });
    });
    svg.appendChild(svgEl("line", { x1: pad.l, y1: pad.t + innerH, x2: width - pad.r, y2: pad.t + innerH, stroke: "#e6e9f0" }));
    data.labels.forEach((label, i) => {
      if (i % Math.ceil(data.labels.length / 7) !== 0) return;
      const step = data.labels.length > 1 ? innerW / (data.labels.length - 1) : innerW;
      const text = svgEl("text", { x: pad.l + i * step, y: height - 12, "text-anchor": "middle", "font-size": "10", fill: "#8b96ab" });
      text.textContent = label;
      svg.appendChild(text);
    });
    host.innerHTML = "";
    host.appendChild(svg);
    const legend = document.createElement("div"); legend.className = "legend mt-2";
    data.series.forEach((s) => {
      const item = document.createElement("span");
      item.innerHTML = `<span class="legend-dot" style="background:${s.color}"></span>${s.name}`;
      legend.appendChild(item);
    });
    host.parentElement.appendChild(legend);
  }

  /* ------------------------------------------------------------ misc helpers */
  function initCounters() {
    $$("[data-counter]").forEach((field) => {
      const wrap = field.closest(".field") || field.parentElement;
      const out = document.createElement("div");
      out.className = "field-help text-right";
      wrap.appendChild(out);
      const update = () => { out.textContent = `${field.value.length} / ${field.maxLength || 2000} characters`; };
      field.addEventListener("input", update);
      update();
    });
  }

  function initTabs() {
    $$("[data-tabs]").forEach((group) => {
      const tabs = $$("[data-tab]", group);
      tabs.forEach((tab) => {
        tab.addEventListener("click", () => {
          tabs.forEach((t) => { t.classList.remove("is-active"); t.setAttribute("aria-selected", "false"); });
          tab.classList.add("is-active");
          tab.setAttribute("aria-selected", "true");
          $$("[data-tab-panel]", document).forEach((panel) => {
            panel.hidden = panel.dataset.tabPanel !== tab.dataset.tab;
          });
        });
      });
    });
  }

  function initCountdowns() {
    const nodes = $$("[data-countdown]");
    if (!nodes.length) return;
    const render = () => {
      const now = Date.now();
      nodes.forEach((node) => {
        const target = new Date(node.dataset.countdown).getTime();
        const diff = target - now;
        if (diff <= 0) { node.textContent = node.dataset.expired || "Closed"; return; }
        const d = Math.floor(diff / 86400000);
        const h = Math.floor((diff % 86400000) / 3600000);
        const m = Math.floor((diff % 3600000) / 60000);
        node.textContent = d > 0 ? `${d}d ${h}h left` : (h > 0 ? `${h}h ${m}m left` : `${m}m left`);
      });
    };
    render();
    setInterval(render, 60000);
  }

  function initPrintButtons() {
    $$("[data-print]").forEach((btn) => btn.addEventListener("click", () => window.print()));
  }

  function initSeatPickers() {
    $$("[data-seat-input]").forEach((input) => {
      const max = Number(input.dataset.max || 0);
      const hint = $(`[data-seat-hint="${input.id}"]`);
      const update = () => {
        const value = Number(input.value || 0);
        if (hint) hint.textContent = max ? `${value} of ${max} seats` : `${value} guests`;
      };
      input.addEventListener("input", update);
      update();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initDismissibleAlerts();
    initSidebar();
    initDropdowns();
    initConfirmations();
    initAsyncFilters();
    initNotifications();
    initCheckIn();
    initStatusSelects();
    initCharts();
    initCounters();
    initTabs();
    initCountdowns();
    initPrintButtons();
    initSeatPickers();
  });
})();
