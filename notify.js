(function () {
  "use strict";
  var uid = document.body.getAttribute("data-uid");
  if (!uid) return;                                   // students only

  var SEEN_KEY = "seen_alerts_" + uid;
  var POP_MS = 12000;                                 // pop-up vanishes after 12 s
  var POLL_MS = 30000;                                // check every 30 s
  var area = document.getElementById("toast-area");

  // ----- remember which alerts were already shown (no repeats) -----
  function loadSeen() {
    try {
      var s = JSON.parse(localStorage.getItem(SEEN_KEY) || "{}");
      var cutoff = Date.now() - 3 * 24 * 3600 * 1000;
      Object.keys(s).forEach(function (k) { if (s[k] < cutoff) delete s[k]; });
      return s;
    } catch (e) { return {}; }
  }
  function saveSeen(s) {
    try { localStorage.setItem(SEEN_KEY, JSON.stringify(s)); } catch (e) { /* ignore */ }
  }

  // ----- in-page pop-up (text is inserted safely, never as HTML) -----
  function showPop(a) {
    if (!area) return;
    while (area.children.length >= 4) area.removeChild(area.firstChild);

    var box = document.createElement("div");
    box.className = "pop " + (a.type || "");
    var t = document.createElement("div");
    t.className = "pop-title"; t.textContent = a.title;
    box.appendChild(t);
    (a.lines || []).forEach(function (line) {
      var d = document.createElement("div");
      d.className = "pop-line"; d.textContent = line;
      box.appendChild(d);
    });
    var x = document.createElement("button");
    x.className = "pop-close"; x.type = "button"; x.setAttribute("aria-label", "Close");
    x.textContent = "\u00d7";
    box.appendChild(x);

    function close() {
      box.classList.add("hide");
      setTimeout(function () { if (box.parentNode) box.parentNode.removeChild(box); }, 450);
    }
    x.addEventListener("click", close);
    box.addEventListener("click", close);
    area.appendChild(box);
    setTimeout(close, POP_MS);
  }

  // ----- optional system notification (laptop / phone) -----
  function systemNotify(a) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    var opts = { body: (a.lines || []).join("\n"), tag: a.id };
    try {
      var n = new Notification(a.title, opts);
      setTimeout(function () { try { n.close(); } catch (e) { /* ignore */ } }, 10000);
    } catch (e) {                                     // Android Chrome needs a service worker
      if (navigator.serviceWorker && navigator.serviceWorker.ready) {
        navigator.serviceWorker.ready
          .then(function (reg) { return reg.showNotification(a.title, opts); })
          .catch(function () { /* ignore */ });
      }
    }
  }

  // ----- ask the server what is coming up -----
  var busy = false;
  function check() {
    if (busy) return;
    busy = true;
    fetch("/api/alerts", { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error("bad status"); return r.json(); })
      .then(function (data) {
        var seen = loadSeen(), changed = false;
        (data.alerts || []).forEach(function (a) {
          if (seen[a.id]) return;
          seen[a.id] = Date.now(); changed = true;
          showPop(a); systemNotify(a);
        });
        if (changed) saveSeen(seen);
      })
      .catch(function () { /* offline or logged out: try again next time */ })
      .then(function () { busy = false; });
  }

  // ----- enable-alerts button + service worker -----
  var btn = document.getElementById("enable-alerts");
  if (btn && "Notification" in window && Notification.permission === "default") {
    btn.classList.remove("d-none");
    btn.addEventListener("click", function () {
      Notification.requestPermission().then(function () { btn.classList.add("d-none"); });
    });
  }
  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/sw.js").catch(function () { /* ignore */ });
  }

  // ----- test button -----
  var test = document.getElementById("test-alert");
  if (test) {
    test.addEventListener("click", function () {
      var a = { id: "test-" + Date.now(), type: "lecture", title: "\uD83D\uDCDA Sample Subject",
                lines: ["Starts in 10 min", "Venue: Room 304", "Faculty: Prof. Sharma"] };
      showPop(a); systemNotify(a);
    });
  }

  check();
  setInterval(check, POLL_MS);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) check(); });
})();
