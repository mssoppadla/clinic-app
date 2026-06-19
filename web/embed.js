/* tovaitech appointments — plug-and-play embed loader.
 *
 * A clinic adds ONE line to their own website:
 *   <script src="https://tovaitech.in/appointments/assets/embed.js"
 *           data-clinic="your-clinic" async></script>
 *
 * This injects a responsive, auto-resizing <iframe> of the hosted booking page.
 * The iframe is cross-origin, so it never clashes with the host site's CSS/JS.
 * Optional attributes:
 *   data-target="#selector"  mount into a specific element (else inserted after the script)
 *   data-min-height="520"    initial height in px before first resize message
 */
(function () {
  var s = document.currentScript;
  if (!s) { var all = document.getElementsByTagName("script"); s = all[all.length - 1]; }

  var slug = s.getAttribute("data-clinic");
  if (!slug) { console.error("[tovai] embed.js: missing data-clinic attribute"); return; }

  var origin = new URL(s.src, location.href).origin;
  var minHeight = parseInt(s.getAttribute("data-min-height"), 10) || 520;

  // Optional per-snippet appearance overrides (clinic edits their own embed line).
  // Saved server-side branding still applies; these layer on top for quick tweaks.
  var qs = "?embed=1";
  ["color", "accent", "headline", "tagline", "book", "header"].forEach(function (k) {
    var v = s.getAttribute("data-" + k);
    if (v !== null && v !== "") qs += "&" + k + "=" + encodeURIComponent(v);
  });

  var iframe = document.createElement("iframe");
  iframe.src = origin + "/appointments/" + encodeURIComponent(slug) + qs;
  iframe.title = "Book an appointment";
  iframe.loading = "lazy";
  iframe.setAttribute("allow", "clipboard-write");
  iframe.style.cssText =
    "width:100%;border:0;display:block;background:transparent;min-height:" + minHeight + "px;";

  var target = s.getAttribute("data-target");
  var mount = target ? document.querySelector(target) : null;
  if (mount) { mount.appendChild(iframe); }
  else { s.parentNode.insertBefore(iframe, s.nextSibling); }

  // Auto-resize: the hosted page posts its content height as it changes.
  window.addEventListener("message", function (e) {
    if (e.origin !== origin) return;
    var d = e.data || {};
    if (d.type === "tovai:resize" && d.height) {
      iframe.style.height = Math.max(d.height, minHeight) + "px";
    }
  });
})();
