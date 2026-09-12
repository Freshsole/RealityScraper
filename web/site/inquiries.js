(() => {
  function toast(message, kind = "ok") {
    let host = document.querySelector(".site-toasts");
    if (!host) {
      host = document.createElement("div");
      host.className = "site-toasts";
      host.setAttribute("aria-live", "polite");
      document.body.appendChild(host);
    }
    const item = document.createElement("div");
    item.className = `site-toast${kind === "error" ? " site-toast-error" : ""}`;
    item.setAttribute("role", "status");
    item.innerHTML = `<span class="site-toast-dot"></span><p></p>`;
    item.querySelector("p").textContent = message;
    host.appendChild(item);
    window.setTimeout(() => item.remove(), 5200);
  }

  function failMessage(body, fallback) {
    const detail = body && body.detail;
    if (typeof detail === "string" && detail) return detail;
    if (Array.isArray(detail) && detail[0]) return detail[0].msg || fallback;
    return fallback;
  }

  async function sendInquiry(form, extra) {
    const data = new FormData(form);
    const payload = {
      jmeno: data.get("jmeno") || "",
      email: data.get("email") || "",
      telefon: data.get("telefon") || "",
      predmet: data.get("predmet") || "",
      zprava: data.get("zprava") || "",
      ...extra,
    };
    const res = await fetch("/api/inquiries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(failMessage(body, "Zprávu se nepodařilo odeslat"));
  }

  async function submitForm(form, extra) {
    const btn = form.querySelector('[type="submit"]');
    const prev = btn?.textContent;
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Odesílám…";
    }
    try {
      await sendInquiry(form, extra);
      form.reset();
      toast("Zpráva byla odeslána.");
      return true;
    } catch (err) {
      toast(err.message || "Zprávu se nepodařilo odeslat", "error");
      return false;
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = prev;
      }
    }
  }

  const contact = document.getElementById("contact-form");
  if (contact) {
    contact.addEventListener("submit", async (event) => {
      event.preventDefault();
      await submitForm(contact, { source: "Kontaktní stránka" });
    });
  }

  window.RfToast = toast;
  window.RfSendInquiryForm = submitForm;
})();
