/* Shared admin navigation drawer. Injects a hamburger (top-left) + a slide-in side panel listing
 * all admin features, grouped. Context-aware:
 *   - clinic pages (/appointments/<slug>/…): the clinic's features, scoped to that slug.
 *   - platform pages / superadmin: a Platform section (approvals, Tovaitech WhatsApp, register).
 * Depends on admin-auth.js for window.CLINIC_SLUG and the session token (role detection).
 */
(function () {
  var Q = new URLSearchParams(location.search);
  var apiSuffix = Q.get("api") ? "?api=" + encodeURIComponent(Q.get("api")) : "";
  var TOKEN_KEY = "tovai_admin_token";

  function roles() {
    try {
      var t = sessionStorage.getItem(TOKEN_KEY);
      if (!t) return [];
      var p = JSON.parse(atob(t.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
      return (p.roles || []).map(function (r) { return r.role; });
    } catch (e) { return []; }
  }
  var isSuper = roles().indexOf("superadmin") >= 0;
  var slug = window.CLINIC_SLUG || "";
  var path = location.pathname;

  function a(href, label, opts) {
    opts = opts || {};
    var ext = opts.external ? ' target="_blank" rel="noopener"' : "";
    var active = (!opts.external && (path === href || path === href + "/")) ? " class=\"np-active\"" : "";
    var suffix = opts.external ? "" : apiSuffix;
    return '<a href="' + href + suffix + '"' + ext + active + ">" + label + "</a>";
  }
  function sec(title, links) {
    return links.length ? '<div class="np-sec">' + title + "</div>" + links.join("") : "";
  }

  var html = "";
  if (slug) {
    var base = "/appointments/" + encodeURIComponent(slug);
    html += sec(slug, [
      a(base + "/slots", "Appointment slots"),
      a(base + "/doctor", "Live queue"),
      a(base + "/users", "Team &amp; users"),
      a(base + "/admin", "WhatsApp &amp; voice"),
      a(base, "Booking page ↗", { external: true }),
    ]);
  }
  if (isSuper) {
    html += sec("Platform", [
      a("/appointments/onboard-admin", "Clinics &amp; approvals"),
      a("/appointments/platform", "Tovaitech WhatsApp"),
      a("/appointments/onboard", "Register a clinic"),
    ]);
  }
  if (!slug && !isSuper) {
    // staff who landed without context — point them home
    html += sec("Account", [a("/appointments/onboard", "Home")]);
  }

  function mount() {
    if (document.getElementById("np-burger")) return;
    var style = document.createElement("style");
    style.textContent =
      "#np-burger{position:fixed;top:8px;left:10px;z-index:60;width:40px;height:40px;border-radius:10px;" +
      "border:1px solid var(--line,#d9e2dc);background:var(--surface,#fff);color:var(--ink,#163a30);" +
      "font-size:1.2rem;line-height:1;cursor:pointer}" +
      "#np-overlay{position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:65;display:none}" +
      "#np-overlay.np-open{display:block}" +
      "#np-drawer{position:fixed;top:0;left:0;height:100%;width:260px;max-width:82vw;z-index:70;" +
      "background:var(--surface,#fff);box-shadow:2px 0 16px rgba(0,0,0,.15);transform:translateX(-100%);" +
      "transition:transform .2s ease;overflow:auto;padding:14px 0}" +
      "#np-drawer.np-open{transform:translateX(0)}" +
      "#np-drawer .np-head{display:flex;align-items:center;justify-content:space-between;padding:4px 16px 12px}" +
      "#np-drawer .np-head b{font-size:1.05rem;color:var(--ink,#163a30)}" +
      "#np-drawer .np-x{border:0;background:none;font-size:1.3rem;cursor:pointer;color:var(--muted,#6b8079)}" +
      "#np-drawer .np-sec{font-size:.7rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted,#6b8079);" +
      "padding:12px 16px 4px}" +
      "#np-drawer a{display:block;padding:9px 16px;color:var(--ink,#163a30);text-decoration:none;font-size:.92rem}" +
      "#np-drawer a:hover{background:var(--bg,#f4f7f5)}" +
      "#np-drawer a.np-active{background:var(--bg,#eef4f1);font-weight:600;border-left:3px solid var(--brand,#0e7c66)}" +
      "#np-drawer .np-foot{margin-top:8px;border-top:1px solid var(--line,#e3eae6);padding-top:6px}" +
      "body.np-shift .top{padding-left:46px}";
    document.head.appendChild(style);

    var burger = document.createElement("button");
    burger.id = "np-burger"; burger.type = "button"; burger.setAttribute("aria-label", "Menu");
    burger.innerHTML = "☰";
    var overlay = document.createElement("div"); overlay.id = "np-overlay";
    var drawer = document.createElement("nav"); drawer.id = "np-drawer";
    drawer.innerHTML =
      '<div class="np-head"><b>Tovaitech</b><button class="np-x" aria-label="Close">×</button></div>' +
      html +
      '<div class="np-foot"><a href="#" id="np-logout">Log out</a></div>';

    document.body.appendChild(overlay);
    document.body.appendChild(drawer);
    document.body.appendChild(burger);
    document.body.classList.add("np-shift");

    function open() { drawer.classList.add("np-open"); overlay.classList.add("np-open"); }
    function close() { drawer.classList.remove("np-open"); overlay.classList.remove("np-open"); }
    burger.onclick = open;
    overlay.onclick = close;
    drawer.querySelector(".np-x").onclick = close;
    drawer.querySelector("#np-logout").onclick = function (e) {
      e.preventDefault();
      if (window.logout) window.logout(); else { sessionStorage.removeItem(TOKEN_KEY); location.reload(); }
    };
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
