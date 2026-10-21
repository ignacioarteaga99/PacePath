// Service worker mínimo de PacePath: hace la app instalable. No guarda nada
// en caché (siempre la versión publicada); sin red, avisa en vez de fallar.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return;            // el resto, directo a la red
  e.respondWith(fetch(e.request).catch(() => new Response(
    `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
     <body style="font-family:system-ui;padding:40px 24px;text-align:center;color:#1d1d21">
     <h2>Sin conexión</h2><p>PacePath necesita internet para armar tus rutas. Vuelve a intentarlo en un momento.</p>
     <p style="color:#6b6b75">No connection · PacePath needs internet to build your routes.</p></body>`,
    { headers: { "Content-Type": "text/html; charset=utf-8" } })));
});
