(() => {
  try {
    if (/^\/admin(?:\/|$)/.test(location.pathname)) return;
    const login = document.querySelector(".nav-login");
    if (login) {
      fetch("/api/auth/me", { credentials: "include" })
        .then((res) => {
          if (!res.ok) return;
          login.classList.add("is-account");
          login.setAttribute("href", "/nastaveni");
          login.innerHTML =
            '<span class="icon" style="width:16px;height:16px"><img src="/static/site/assets/stories/user.svg" width="16" height="16" alt="" /></span>Můj účet';
        })
        .catch(() => {});
    }
    const menuLinks = [...document.querySelectorAll(".navbar .menu a")];
    const markMenu = (active) => {
      menuLinks.forEach((link) => link.classList.toggle("menu-active", link === active));
    };
    const path = location.pathname.replace(/\/+$/, "") || "/";
    if (path.startsWith("/uspechy")) {
      markMenu(menuLinks.find((link) => new URL(link.href, location.origin).pathname.startsWith("/uspechy")) || null);
    } else if (path === "/") {
      const sections = menuLinks
        .map((link) => {
          const id = (link.getAttribute("href") || "").split("#")[1];
          return id ? { link, el: document.getElementById(id) } : null;
        })
        .filter((item) => item?.el);
      const spy = () => {
        let current = null;
        for (const item of sections) {
          if (item.el.getBoundingClientRect().top <= 120) current = item;
        }
        markMenu(current?.link || null);
      };
      menuLinks.forEach((link) => {
        if ((link.getAttribute("href") || "").includes("#")) {
          link.addEventListener("click", () => markMenu(link));
        }
      });
      window.addEventListener("scroll", spy, { passive: true });
      window.addEventListener("hashchange", spy);
      spy();
    }
    const consentCookie = document.cookie.split("; ").find((part) => part.startsWith("rf_consent="));
    if (consentCookie) {
      try {
        const data = JSON.parse(decodeURIComponent(consentCookie.slice("rf_consent=".length)));
        if (data.a === 0 || data.analytics === false) return;
      } catch {
        /* keep tracking if consent cannot be parsed */
      }
    }
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
