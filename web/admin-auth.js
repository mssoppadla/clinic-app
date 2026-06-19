/* Shared staff/admin auth for the gated admin pages.
 * authFetch() adds the Bearer token (login overlay on 401); requireLogin() gates a page.
 * Overlay handles: sign-in, forced first-login password change, and WhatsApp-OTP reset.
 * Token in sessionStorage (cleared on tab close / 401).
 */
(function () {
  var Q = new URLSearchParams(location.search);
  var API = (Q.get("api") || window.API_BASE ||
    (location.port === "8080" ? "http://localhost:8077" : "/api/v1")).replace(/\/$/, "");
  var KEY = "tovai_admin_token";
  var token = function () { return sessionStorage.getItem(KEY) || ""; };

  async function postJSON(path, body, auth) {
    var h = { "Content-Type": "application/json" };
    if (auth && token()) h.Authorization = "Bearer " + token();
    var r = await fetch(API + path, { method: "POST", headers: h, body: JSON.stringify(body) });
    var data = {};
    try { data = await r.json(); } catch (e) {}
    return { ok: r.ok, status: r.status, data: data };
  }
  function msg(data, fallback) { return (data && data.error && data.error.message) || fallback; }

  function box(inner) {
    var d = document.getElementById("loginOverlay");
    if (!d) { d = document.createElement("div"); d.id = "loginOverlay"; document.body.appendChild(d); }
    d.innerHTML = '<div class="loginbox"><div class="brandlogo" style="margin-bottom:10px">T</div>' + inner + "</div>";
    return d;
  }
  function field(id, label, type) {
    return '<div class="field"><label>' + label + '</label><input class="input" id="' + id + '" type="' + (type || "text") + '"/></div>';
  }
  function errLine() { return '<div id="ovErr" class="err hidden"></div>'; }
  function showErr(t) { var e = document.getElementById("ovErr"); if (e) { e.textContent = t; e.classList.remove("hidden"); } }

  function loginView() {
    box('<h3 style="margin:0 0 2px">Staff sign in</h3><p class="hint">Use your tovaitech staff account.</p>'
      + errLine() + field("ovEmail", "Email or username", "text") + field("ovPass", "Password", "password")
      + '<button class="btn" id="ovBtn" type="button">Sign in</button>'
      + '<p class="foot"><a href="#" id="ovForgot">Forgot password?</a></p>');
    document.getElementById("ovBtn").onclick = doLogin;
    document.getElementById("ovForgot").onclick = function (e) { e.preventDefault(); forgotView(); };
    document.getElementById("ovPass").addEventListener("keydown", function (e) { if (e.key === "Enter") doLogin(); });
  }
  async function doLogin() {
    var ident = document.getElementById("ovEmail").value.trim();
    var pass = document.getElementById("ovPass").value;
    var r = await postJSON("/auth/login", { identifier: ident, password: pass });
    if (!r.ok) { showErr(msg(r.data, "Sign in failed")); return; }
    sessionStorage.setItem(KEY, r.data.access_token);
    if (r.data.must_reset_password) { mustResetView(pass); } else { location.reload(); }
  }

  function mustResetView(currentPw) {
    box('<h3 style="margin:0 0 2px">Set a new password</h3><p class="hint">Your account requires a new password.</p>'
      + errLine() + field("ovNew", "New password", "password") + field("ovNew2", "Confirm new password", "password")
      + '<button class="btn" id="ovBtn" type="button">Save & continue</button>');
    document.getElementById("ovBtn").onclick = async function () {
      var n1 = document.getElementById("ovNew").value, n2 = document.getElementById("ovNew2").value;
      if (n1.length < 8) { showErr("Password must be at least 8 characters."); return; }
      if (n1 !== n2) { showErr("Passwords don't match."); return; }
      var r = await postJSON("/auth/change-password", { current_password: currentPw, new_password: n1 }, true);
      if (!r.ok) { showErr(msg(r.data, "Could not set password")); return; }
      location.reload();
    };
  }

  function forgotView() {
    box('<h3 style="margin:0 0 2px">Reset password</h3><p class="hint">We\'ll send a code to your WhatsApp.</p>'
      + errLine() + field("ovEmail", "Email or username", "text")
      + '<button class="btn" id="ovBtn" type="button">Send code</button>'
      + '<p class="foot"><a href="#" id="ovBack">Back to sign in</a></p>');
    document.getElementById("ovBack").onclick = function (e) { e.preventDefault(); loginView(); };
    document.getElementById("ovBtn").onclick = async function () {
      var ident = document.getElementById("ovEmail").value.trim();
      var r = await postJSON("/auth/forgot", { identifier: ident });
      if (!r.ok) { showErr(msg(r.data, "Could not send code")); return; }
      resetView(ident);
    };
  }
  function resetView(ident) {
    box('<h3 style="margin:0 0 2px">Enter reset code</h3><p class="hint">Code sent to the WhatsApp number on file (if the account exists).</p>'
      + errLine() + field("ovOtp", "6-digit code") + field("ovNew", "New password", "password")
      + '<button class="btn" id="ovBtn" type="button">Reset password</button>'
      + '<p class="foot"><a href="#" id="ovBack">Back to sign in</a></p>');
    document.getElementById("ovBack").onclick = function (e) { e.preventDefault(); loginView(); };
    document.getElementById("ovBtn").onclick = async function () {
      var otp = document.getElementById("ovOtp").value.trim(), n = document.getElementById("ovNew").value;
      if (n.length < 8) { showErr("Password must be at least 8 characters."); return; }
      var r = await postJSON("/auth/reset", { identifier: ident, otp: otp, new_password: n });
      if (!r.ok) { showErr(msg(r.data, "Invalid or expired code")); return; }
      loginView(); showErr(""); document.getElementById("ovErr").classList.add("hidden");
    };
  }

  window.requireLogin = function () { if (!token()) loginView(); };
  window.authFetch = async function (url, opts) {
    opts = opts || {};
    opts.headers = Object.assign({}, opts.headers || {}, token() ? { Authorization: "Bearer " + token() } : {});
    var r = await fetch(url, opts);
    if (r.status === 401) { sessionStorage.removeItem(KEY); if (!document.getElementById("loginOverlay")) loginView(); }
    return r;
  };
})();
