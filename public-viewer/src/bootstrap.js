// Remove capabilities before loading the map libraries or making API requests.
(() => {
  let secret = location.hash.slice(1);
  const hadFragment = location.hash.length > 0;
  history.replaceState(null, "", location.pathname);
  const observer = new MutationObserver(async () => {
    if (!document.getElementById("map")) return;
    observer.disconnect();
    try {
      const { start } = await import("./app.js");
      const capability = secret;
      secret = null;
      await start(capability, hadFragment);
    } catch {
      secret = null;
      document.getElementById("status").textContent =
        "The viewer could not start. Reload the original share link to try again.";
      document.getElementById("status").setAttribute("role", "alert");
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
