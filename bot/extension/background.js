// Relais entre les pages (Leboncoin, Vinted, eBay) et le logiciel Chasseur d'affaires lancé sur ce PC.
// Le logiciel écoute sur le premier port libre à partir de 8000 : on le cherche puis on le mémorise.
let port = null;

async function post(p, body) {
  const r = await fetch(`http://localhost:${p}/api/import`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

async function send(body) {
  const ports = port ? [port] : [];
  for (let p = 8000; p < 8020; p++) if (p !== port) ports.push(p);
  for (const p of ports) {
    try {
      const res = await post(p, body);
      port = p;
      return { ok: true, port: p, ...res };
    } catch (e) { /* port suivant */ }
  }
  port = null;
  return { ok: false, error: "Logiciel Chasseur d'affaires non lancé" };
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg && msg.type === "import") {
    send({ source: msg.source, listings: msg.listings }).then(reply);
    return true; // réponse asynchrone
  }
});
