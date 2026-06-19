/* Shared staff/admin auth for the gated admin pages (integrations, approvals).
 * Provides authFetch() (adds the Bearer token, shows a login overlay on 401) and
 * requireLogin(). Token kept in sessionStorage (cleared on tab close / 401).
 */
(function () {
  var Q = new URLSearchParams(location.search);
  var API = (Q.get("api") || window.API_BASE ||
    (location.port === "8080" ? "http://localhost:8077" : "/api/v1")).replace(/\/$/, "");
  var KEY = "tovai_admin_token";
  var token = function () { return sessionStorage.getItem(KEY) || ""; };

  function overlay() {
    var d = document.createElement("div");
    d.id = "loginOverlay";
    d.innerHTML =
      '<div class="loginbox">' +
      '<div class="brandlogo" style="margin-bottom:10px">T</div>' +
      '<h3 style="margin:0 0 2px">Staff sign in</h3>' +
      '<p class="hint">Use your tovaitech staff account.</p>' +
      '<div id="loginErr" class="err hidden"></div>' +
      '<div class="field"><label>Email</label><input class="input" id="loginEmail" type="email" autocomplete="username"/></div>' +
      '<div class="field"><label>Password</label><input class="input" id="loginPass" type="password" autocomplete="current-password"/></div>' +
      '<button class="btn" id="loginBtn" type="button">Sign in</button>' +
      "</div>";
    document.body.appendChild(d);
    var err = d.querySelector("#loginErr");
    d.querySelector("#loginBtn").onclick = async function () {
      err.classList.add("hidden");
      var email = d.querySelector("#loginEmail").value.trim();
      var pass = d.querySelector("#loginPass").value;
      try {
        var r = await fetch(API + "/auth/login", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: email, password: pass }),
        });
        var data = await r.json();
        if (!r.ok) { err.textContent = (data.error && data.error.message) || "Sign in failed"; err.classList.remove("hidden"); return; }
        sessionStorage.setItem(KEY, data.access_token);
        location.reload();
      } catch (e) { err.textContent = "Network error — try again."; err.classList.remove("hidden"); }
    };
    d.querySelector("#loginPass").addEventListener("keydown", function (e) { if (e.key === "Enter") d.querySelector("#loginBtn").click(); });
  }

  window.requireLogin = function () { if (!token()) overlay(); };

  window.authFetch = async function (url, opts) {
    opts = opts || {};
    opts.headers = Object.assign({}, opts.headers || {}, token() ? { Authorization: "Bearer " + token() } : {});
    var r = await fetch(url, opts);
    if (r.status === 401) { sessionStorage.removeItem(KEY); if (!document.getElementById("loginOverlay")) overlay(); }
    return r;
  };
})();
