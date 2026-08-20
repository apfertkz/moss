/* Aisaty CRM. Ванильный JS: доска, карточка, команда. Без сборки. */
(function () {
  "use strict";

  var $app = document.getElementById("app");
  var state = { me: null, board: null, open: null, loginToken: null };
  var MARK = "/crm-static/mark.svg";

  /* ───────── api ───────── */

  function api(path, data) {
    return fetch("/api/crm" + path, {
      method: data === undefined ? "GET" : "POST",
      headers: data === undefined ? {} : { "Content-Type": "application/json" },
      body: data === undefined ? undefined : JSON.stringify(data),
      credentials: "same-origin",
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok || d.error) throw new Error(d.error || "Сервер не ответил");
        return d;
      });
    });
  }

  function toast(msg, err) {
    var t = document.createElement("div");
    t.className = "toast" + (err ? " err" : "");
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2600);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function money(n) {
    return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " ₸";
  }

  function phoneLinks(contact) {
    var c = String(contact || "").trim();
    var digits = c.replace(/[^\d+]/g, "");
    var out = [];
    if (/^\+?\d{10,15}$/.test(digits)) {
      var e164 = digits[0] === "+" ? digits : (digits[0] === "8" ? "+7" + digits.slice(1) : "+" + digits);
      out.push({ label: "Позвонить", href: "tel:" + e164 });
      out.push({ label: "WhatsApp", href: "https://wa.me/" + e164.replace("+", "") });
    }
    if (/^@\w+/.test(c)) out.push({ label: "Telegram", href: "https://t.me/" + c.slice(1) });
    return out;
  }

  /* ───────── вход ───────── */

  function renderLogin(step, err) {
    $app.innerHTML =
      '<div class="login"><div class="login-card">' +
      '<img src="' + MARK + '" alt="">' +
      "<h1>Aisaty CRM</h1>" +
      "<p>" + (step === "code"
        ? "Код ушёл вам в Telegram. Он живёт пять минут."
        : "Вход по Telegram ID — бот пришлёт код.") + "</p>" +
      '<form id="lf">' +
      (step === "code"
        ? '<input class="code" id="lin" inputmode="numeric" maxlength="6" placeholder="••••••" autofocus>'
        : '<input id="lin" inputmode="numeric" placeholder="Telegram ID" autofocus>') +
      '<button class="btn btn-acc" type="submit">→</button>' +
      "</form>" +
      (err ? '<div class="login-err">' + esc(err) + "</div>" : "") +
      "</div></div>";

    document.getElementById("lf").addEventListener("submit", function (e) {
      e.preventDefault();
      var v = document.getElementById("lin").value.trim();
      if (step === "code") {
        api("/verify", { token: state.loginToken, code: v })
          .then(function () { boot(); })
          .catch(function (er) { renderLogin("code", er.message); });
      } else {
        api("/login", { tg: v })
          .then(function (d) { state.loginToken = d.token; renderLogin("code"); })
          .catch(function (er) { renderLogin("tg", er.message); });
      }
    });
  }

  /* ───────── доска ───────── */

  function leadCard(l) {
    var demo = l.demo_result === "won" ? '<span class="tag won">сделка ✓</span>'
      : l.demo_result === "lost" ? '<span class="tag lost">слил</span>' : "";
    var pilot = l.company_id
      ? '<span class="tag' + (l.sessions_used > 0 ? " pilot-on" : "") + '">' +
        (l.sessions_used > 0 ? "тренируется · " + l.sessions_used : "не начал") + "</span>"
      : "";
    var when = l.created_at ? new Date(l.created_at).toLocaleDateString("ru-RU",
      { day: "numeric", month: "short" }) : "";
    return '<div class="card" draggable="true" data-id="' + l.id + '">' +
      "<b>" + esc(l.name || "Без имени") + "</b>" +
      '<div class="c-contact">' + esc(l.contact || "—") + "</div>" +
      (l.niche ? '<div class="c-niche">' + esc(l.niche) + "</div>" : "") +
      '<div class="c-meta"><span>' + when + "</span>" + demo + pilot +
      (l.source === "manual" ? '<span class="tag">вручную</span>' : "") +
      "</div></div>";
  }

  function renderBoard() {
    var b = state.board;
    var byStatus = {};
    b.columns.forEach(function (c) { byStatus[c.key] = []; });
    b.leads.forEach(function (l) {
      (byStatus[l.status] || byStatus.new).push(l);
    });

    $app.innerHTML =
      '<header class="top">' +
      '<img src="' + MARK + '" alt=""><b>Aisaty CRM</b>' +
      '<span class="chip">' + b.leads.length + " лидов</span>" +
      '<span class="chip">' + b.leads.filter(function (l) { return l.status === "new"; }).length + " новых</span>" +
      '<span class="sp"></span>' +
      '<button class="btn btn-acc btn-sm" id="addLead">+ Лид</button>' +
      (state.me.role === "owner" ? '<button class="btn btn-ghost btn-sm" id="teamBtn">Команда</button>' : "") +
      '<a class="btn btn-ghost btn-sm" href="/api/crm/export">CSV</a>' +
      '<button class="btn btn-ghost btn-sm" id="logout">Выйти</button>' +
      "</header>" +
      '<main class="board">' +
      b.columns.map(function (c) {
        var cards = byStatus[c.key] || [];
        return '<section class="col" data-status="' + c.key + '">' +
          '<div class="col-head">' + esc(c.title) + '<span class="n">' + cards.length + "</span></div>" +
          '<div class="col-body">' + cards.map(leadCard).join("") + "</div></section>";
      }).join("") +
      "</main>";

    document.getElementById("addLead").onclick = openNewLead;
    document.getElementById("logout").onclick = function () {
      api("/logout", {}).then(function () { renderLogin("tg"); });
    };
    var tb = document.getElementById("teamBtn");
    if (tb) tb.onclick = openTeam;

    /* клик по карточке */
    $app.querySelectorAll(".card").forEach(function (el) {
      el.addEventListener("click", function () { openLead(+el.dataset.id); });
      el.addEventListener("dragstart", function (e) {
        el.classList.add("dragging");
        e.dataTransfer.setData("text/plain", el.dataset.id);
        e.dataTransfer.effectAllowed = "move";
      });
      el.addEventListener("dragend", function () { el.classList.remove("dragging"); });
    });

    /* перетаскивание в колонку */
    $app.querySelectorAll(".col-body").forEach(function (zone) {
      zone.addEventListener("dragover", function (e) { e.preventDefault(); zone.classList.add("drag-over"); });
      zone.addEventListener("dragleave", function () { zone.classList.remove("drag-over"); });
      zone.addEventListener("drop", function (e) {
        e.preventDefault();
        zone.classList.remove("drag-over");
        var id = +e.dataTransfer.getData("text/plain");
        var status = zone.parentElement.dataset.status;
        moveLead(id, status);
      });
    });
  }

  function moveLead(id, status) {
    var l = state.board.leads.find(function (x) { return x.id === id; });
    if (!l || l.status === status) return;
    var old = l.status;
    l.status = status;
    renderBoard();
    api("/leads/" + id, { status: status }).catch(function (e) {
      l.status = old; renderBoard(); toast(e.message, true);
    });
  }

  /* ───────── карточка лида ───────── */

  function overlay(html) {
    var o = document.createElement("div");
    o.className = "overlay";
    o.innerHTML = '<div class="modal">' + html + "</div>";
    o.addEventListener("click", function (e) { if (e.target === o) close(); });
    function close() { o.remove(); document.removeEventListener("keydown", onKey); }
    function onKey(e) { if (e.key === "Escape") close(); }
    document.addEventListener("keydown", onKey);
    document.body.appendChild(o);
    o.close = close;
    return o;
  }

  function openLead(id) {
    var l = state.board.leads.find(function (x) { return x.id === id; });
    if (!l) return;
    var b = state.board;
    var links = phoneLinks(l.contact);
    var planOpts = b.plans.map(function (p) {
      return '<option value="' + p.key + '"' + (l.plan_key === p.key ? " selected" : "") + ">" +
        esc(p.title) + " · " + money(p.price_kzt) + "/мес</option>";
    }).join("");
    var monthOpts = [1, 3, 6, 12].map(function (m) {
      return '<option value="' + m + '"' + ((l.months || 1) === m ? " selected" : "") + ">" + m + " мес</option>";
    }).join("");
    var userOpts = '<option value="">—</option>' + (b.users || []).map(function (u) {
      return '<option value="' + u.telegram_id + '"' + (l.assignee === u.telegram_id ? " selected" : "") + ">" +
        esc(u.name || u.telegram_id) + "</option>";
    }).join("");
    var statusOpts = b.columns.map(function (c) {
      return '<option value="' + c.key + '"' + (l.status === c.key ? " selected" : "") + ">" + esc(c.title) + "</option>";
    }).join("");

    var o = overlay(
      '<div class="m-head"><h2>' + esc(l.name || "Без имени") + "</h2>" +
      '<button class="x">✕</button></div>' +
      '<div class="m-grid">' +
      '<div class="f"><label>Имя</label><input id="f-name" value="' + esc(l.name) + '"></div>' +
      '<div class="f"><label>Контакт</label><input id="f-contact" value="' + esc(l.contact) + '"></div>' +
      '<div class="f full"><label>Ниша</label><input id="f-niche" value="' + esc(l.niche) + '"></div>' +
      '<div class="f"><label>Статус</label><select id="f-status">' + statusOpts + "</select></div>" +
      '<div class="f"><label>Ответственный</label><select id="f-assignee">' + userOpts + "</select></div>" +
      '<div class="f"><label>Тариф</label><select id="f-plan">' + planOpts + "</select></div>" +
      '<div class="f"><label>Срок</label><select id="f-months">' + monthOpts + "</select></div>" +
      "</div>" +
      (links.length ? '<div class="m-links">' + links.map(function (a) {
        return '<a href="' + a.href + '" target="_blank" rel="noopener">' + a.label + "</a>";
      }).join("") + "</div>" : "") +
      (l.demo_verdict
        ? '<div class="m-block"><h3>Демо · ' +
          (l.demo_result === "won" ? "сделка закрыта" : "сделка потеряна") +
          " · ходов: " + (l.demo_turns || 0) + '</h3><div class="verdict">' +
          esc(l.demo_verdict) + "</div></div>"
        : "") +
      (l.company_id
        ? '<div class="m-block m-company"><h3>Компания</h3>' +
          '<div class="row"><span>Название</span><b>' + esc(l.company_title || "") + "</b></div>" +
          '<div class="row"><span>Тренировок</span><b>' + (l.sessions_used || 0) + " / " +
          (l.company_limit || "∞") + "</b></div>" +
          '<div class="row"><span>Доступ до</span><b>' +
          (l.expires_at ? new Date(l.expires_at).toLocaleDateString("ru-RU") : "—") + "</b></div>" +
          (l.activation_link
            ? '<div class="m-links" style="margin:10px 0 0"><a href="#" id="copyLink">Скопировать ссылку активации</a></div>'
            : "") +
          "</div>"
        : "") +
      '<div class="m-note f"><label>Заметка</label><textarea id="f-note">' + esc(l.note) + "</textarea></div>" +
      '<div id="invoiceBox"></div>' +
      '<div class="m-actions">' +
      '<button class="btn btn-acc" id="saveBtn">Сохранить</button>' +
      (!l.company_id ? '<button class="btn btn-ghost" id="grantBtn">Выдать пилот</button>' : "") +
      '<button class="btn btn-ghost" id="invoiceBtn">Счёт</button>' +
      (l.company_id ? '<button class="btn btn-ghost" id="paidBtn">Оплачено</button>' : "") +
      "</div>"
    );
    o.querySelector(".x").onclick = o.close;

    var copyEl = o.querySelector("#copyLink");
    if (copyEl) copyEl.onclick = function (e) {
      e.preventDefault();
      navigator.clipboard.writeText(l.activation_link).then(function () { toast("Ссылка скопирована"); });
    };

    o.querySelector("#saveBtn").onclick = function () {
      api("/leads/" + l.id, {
        name: o.querySelector("#f-name").value,
        contact: o.querySelector("#f-contact").value,
        niche: o.querySelector("#f-niche").value,
        note: o.querySelector("#f-note").value,
        status: o.querySelector("#f-status").value,
        assignee: o.querySelector("#f-assignee").value || null,
        plan_key: o.querySelector("#f-plan").value,
        months: +o.querySelector("#f-months").value,
      }).then(function () { o.close(); refresh(); toast("Сохранено"); })
        .catch(function (e) { toast(e.message, true); });
    };

    var g = o.querySelector("#grantBtn");
    if (g) g.onclick = function () {
      g.disabled = true;
      api("/leads/" + l.id + "/grant", { title: o.querySelector("#f-name").value, plan: "trial" })
        .then(function (d) {
          navigator.clipboard.writeText(d.link).catch(function () {});
          toast(d.sent ? "Пилот выдан, ссылка ушла в Telegram" : "Пилот выдан — ссылка в буфере");
          o.close(); refresh();
        })
        .catch(function (e) { g.disabled = false; toast(e.message, true); });
    };

    o.querySelector("#invoiceBtn").onclick = function () {
      api("/leads/" + l.id + "/invoice", {
        plan_key: o.querySelector("#f-plan").value,
        months: +o.querySelector("#f-months").value,
      }).then(function (d) {
        var box = o.querySelector("#invoiceBox");
        box.innerHTML = '<div class="invoice-box">' + esc(d.text) + "</div>" +
          '<div class="m-links" style="margin-top:8px"><a href="#" id="copyInv">Скопировать счёт</a></div>';
        box.querySelector("#copyInv").onclick = function (e) {
          e.preventDefault();
          navigator.clipboard.writeText(d.text).then(function () { toast("Счёт скопирован — отправьте клиенту"); });
        };
      }).catch(function (e) { toast(e.message, true); });
    };

    var pd = o.querySelector("#paidBtn");
    if (pd) pd.onclick = function () {
      if (!confirm("Записать оплату и продлить доступ?")) return;
      pd.disabled = true;
      api("/leads/" + l.id + "/paid", {
        plan_key: o.querySelector("#f-plan").value,
        months: +o.querySelector("#f-months").value,
      }).then(function (d) {
        toast("Оплата " + money(d.amount_kzt) + " записана, доступ продлён");
        o.close(); refresh();
      }).catch(function (e) { pd.disabled = false; toast(e.message, true); });
    };
  }

  /* ───────── новый лид ───────── */

  function openNewLead() {
    var o = overlay(
      '<div class="m-head"><h2>Новый лид</h2><button class="x">✕</button></div>' +
      '<div class="m-grid">' +
      '<div class="f"><label>Имя</label><input id="n-name" autofocus></div>' +
      '<div class="f"><label>Контакт</label><input id="n-contact" placeholder="+7 701 … или @ник"></div>' +
      '<div class="f full"><label>Ниша</label><input id="n-niche"></div>' +
      '<div class="f full"><label>Заметка</label><textarea id="n-note"></textarea></div>' +
      "</div>" +
      '<div class="m-actions"><button class="btn btn-acc" id="createBtn">Создать</button></div>');
    o.querySelector(".x").onclick = o.close;
    o.querySelector("#createBtn").onclick = function () {
      api("/leads", {
        name: o.querySelector("#n-name").value,
        contact: o.querySelector("#n-contact").value,
        niche: o.querySelector("#n-niche").value,
        note: o.querySelector("#n-note").value,
      }).then(function () { o.close(); refresh(); toast("Лид создан"); })
        .catch(function (e) { toast(e.message, true); });
    };
  }

  /* ───────── команда ───────── */

  function openTeam() {
    api("/users").then(function (users) {
      var o = overlay(
        '<div class="m-head"><h2>Команда</h2><button class="x">✕</button></div>' +
        '<div id="teamList">' + users.filter(function (u) { return u.active; }).map(function (u) {
          return '<div class="team-row"><b>' + esc(u.name || u.telegram_id) + "</b>" +
            '<span class="role">' + (u.role === "owner" ? "владелец" : "менеджер") +
            " · " + u.telegram_id + "</span>" +
            '<button class="rm" data-tg="' + u.telegram_id + '">убрать</button></div>';
        }).join("") + "</div>" +
        '<div class="team-add">' +
        '<input id="t-tg" inputmode="numeric" placeholder="Telegram ID">' +
        '<input id="t-name" placeholder="Имя">' +
        '<button class="btn btn-acc" id="t-add">Добавить</button>' +
        "</div>" +
        '<p style="color:var(--faint);font-size:12px;margin-top:10px">' +
        "Менеджер должен один раз написать боту, чтобы получать коды и уведомления. " +
        "ID узнаётся командой /id в боте.</p>");
      o.querySelector(".x").onclick = o.close;
      o.querySelector("#t-add").onclick = function () {
        api("/users", { tg: o.querySelector("#t-tg").value, name: o.querySelector("#t-name").value })
          .then(function () { o.close(); toast("Менеджер добавлен"); refresh(); })
          .catch(function (e) { toast(e.message, true); });
      };
      o.querySelectorAll(".rm").forEach(function (btn) {
        btn.onclick = function () {
          api("/users/" + btn.dataset.tg + "/remove", {})
            .then(function () { o.close(); toast("Убран"); refresh(); })
            .catch(function (e) { toast(e.message, true); });
        };
      });
    }).catch(function (e) { toast(e.message, true); });
  }

  /* ───────── загрузка ───────── */

  function refresh() {
    return api("/board").then(function (b) { state.board = b; renderBoard(); });
  }

  function boot() {
    api("/me").then(function (me) {
      if (!me.authorized) { renderLogin("tg"); return; }
      state.me = me;
      refresh().catch(function (e) { toast(e.message, true); });
      /* доска живёт: раз в минуту тихо обновляемся, если нет открытых окон */
      setInterval(function () {
        if (!document.querySelector(".overlay")) refresh().catch(function () {});
      }, 60000);
    }).catch(function () { renderLogin("tg"); });
  }

  boot();
})();
