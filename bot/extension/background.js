// Relais entre les pages (Leboncoin, Vinted, eBay) et le logiciel Chasseur d'affaires lancé sur ce PC.
// Le logiciel écoute sur 127.0.0.1, premier port libre à partir de 8000 : on le cherche puis on le mémorise.
let port = null;

async function call(p, path, body) {
  const r = await fetch(`http://127.0.0.1:${p}${path}`, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

async function withPort(path, body) {
  const ports = port ? [port] : [];
  for (let p = 8000; p < 8020; p++) if (p !== port) ports.push(p);
  for (const p of ports) {
    try {
      let res;
      if (path === "/api/ping") {  // anciennes versions du logiciel : pas de /api/ping, on teste /api/deals
        try { res = await call(p, path); } catch (e) { res = await call(p, "/api/deals"); res = res.stats ? { app: "chasseur" } : {}; }
        if (res.app !== "chasseur") continue;
      } else res = await call(p, path, body);
      port = p;
      return { ok: true, port: p, ...res };
    } catch (e) { /* port suivant */ }
  }
  port = null;
  return { ok: false, error: "Logiciel Chasseur d'affaires non lancé (ouvrez ChasseurAffaires.exe)" };
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg && msg.type === "import") {
    withPort("/api/import", { source: msg.source, listings: msg.listings }).then(reply);
    return true;
  }
  if (msg && msg.type === "ping") {
    withPort("/api/ping").then(reply);
    return true;
  }
});
