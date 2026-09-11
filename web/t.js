(() => {
  try {
    if (/^\/admin(?:\/|$)/.test(location.pathname)) return;
    const ping = (heartbeat) => {
      fetch("/api/t", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        keepalive: true,
        body: JSON.stringify({
          path: location.pathname,
          tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
          lang: navigator.language || "",
          w: window.innerWidth,
          ua: navigator.userAgent,
          heartbeat: Boolean(heartbeat),
        }),
      }).catch(() => {});
    };
    ping(false);
    setInterval(() => {
      if (!document.hidden) ping(true);
    }, 20000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) ping(true);
    });
  } catch {
    /* ignore */
  }
})();
