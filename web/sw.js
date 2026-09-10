self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "Realitify";
  const options = {
    body: data.body || "Nová nabídka na trhu",
    icon: data.icon || "/static/site/assets/logo.svg",
    badge: "/static/site/assets/logo.svg",
    data: { url: data.url || "/nabidka" },
    tag: data.tag || "realitify",
    renotify: true,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = event.notification.data && event.notification.data.url ? event.notification.data.url : "/nabidka";
  event.waitUntil(openTarget(target));
});

async function openTarget(url) {
  const abs = new URL(url, self.location.origin).href;
  const windows = await clients.matchAll({ type: "window", includeUncontrolled: true });
  for (const client of windows) {
    if (client.url.startsWith(self.location.origin) && "focus" in client) {
      await client.focus();
      if ("navigate" in client) {
        try {
          await client.navigate(abs);
        } catch {
          /* Safari may block navigate */
        }
      }
      return;
    }
  }
  if (clients.openWindow) await clients.openWindow(abs);
}
