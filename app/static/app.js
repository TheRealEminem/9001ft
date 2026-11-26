// Registers the service worker that makes 9001ft installable and shows a
// friendly page when you're offline. It never stores articles.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
  });
}
