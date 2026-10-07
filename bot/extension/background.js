// Relais entre les pages (Leboncoin, Vinted, eBay) et le logiciel Chasseur d'affaires lancé sur ce PC.
// Le logiciel écoute sur 127.0.0.1, premier port libre à partir de 8000 : on le cherche puis on le mémorise.
let port = null;

async function call(p, path, body) {
  const r = await fetch(`http://127.0.0.1:${p}${path}`, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) {
    let msg = "HTTP " + r.status;
    try { msg = (await r.json()).error || msg; } catch (e) {}
    const err = new Error(msg); err.answered = true; throw err;
  }
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
      return Array.isArray(res) ? { ok: true, port: p, list: res } : { ok: true, port: p, ...res };
    } catch (e) {
      if (e.answered && path !== "/api/ping") { port = p; return { ok: false, port: p, error: e.message }; }
      /* pas de réponse : port suivant */
    }
  }
  port = null;
  return { ok: false, error: "Logiciel Chasseur d'affaires non lancé (ouvrez ChasseurAffaires.exe)" };
}

// ---- Tournée : le logiciel tient la liste des recherches ; chaque clic (ou raccourci) ouvre la suivante ----
async function tourNext(step, site) {
  return withPort("/api/tour/next", { step, site });
}

async function tourStart(sites, topOnly) {
  return withPort("/api/tour/start", { sites, top_only: topOnly });
}

function siteOf(url) {
  return /vinted\./.test(url || "") ? "vinted" : /ebay\./.test(url || "") ? "ebay" : "leboncoin";
}

async function openInTab(url, tab) {
  const onSite = tab && /leboncoin\.fr|vinted\.fr|ebay\.(fr|de)/.test(tab.url || "");
  if (onSite) await chrome.tabs.update(tab.id, { url }); else await chrome.tabs.create({ url });
}

async function finishTour(res) {
  const url = `http://127.0.0.1:${res.port || port || 8000}/`;
  chrome.notifications.create("tour-fin", {
    type: "basic", iconUrl: "icon128.png", title: "Tournée terminée",
    message: `${res.good} bonne(s) affaire(s) trouvée(s) pendant la tournée. Cliquez pour les voir.`,
  });
  await chrome.storage.local.set({ current: `Tournée terminée : ${res.good} bonne(s) affaire(s)`, botUrl: url });
}

// Raccourcis clavier (Alt+Maj+→ / Alt+Maj+←) : comme le bouton « Suivante » de l'encadré
chrome.commands.onCommand.addListener(async command => {
  const step = command === "tour-prev" ? -1 : 1;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const res = await tourNext(step, siteOf(tab && tab.url));
  if (!res.ok) return;
  if (res.finished) return finishTour(res);
  await chrome.storage.local.set({ current: res.label });
  openInTab(res.url, tab);
});

// ---- Rappel de tournée (9 h et 18 h par défaut), désactivable dans la fenêtre de l'extension ----
const REMINDERS = [[9, 0], [18, 0]];

function nextTime(h, m) {
  const d = new Date(); d.setHours(h, m, 0, 0);
  if (d <= new Date()) d.setDate(d.getDate() + 1);
  return d.getTime();
}

async function scheduleReminders() {
  const { reminder = true } = await chrome.storage.local.get("reminder");
  await chrome.alarms.clearAll();
  if (!reminder) return;
  REMINDERS.forEach(([h, m], i) => chrome.alarms.create(`tour-${i}`, { when: nextTime(h, m), periodInMinutes: 24 * 60 }));
}

chrome.runtime.onInstalled.addListener(scheduleReminders);
chrome.runtime.onStartup.addListener(scheduleReminders);
chrome.alarms.onAlarm.addListener(alarm => {
  if (!alarm.name.startsWith("tour-")) return;
  chrome.notifications.create("tour-rappel", {
    type: "basic", iconUrl: "icon128.png", title: "C'est l'heure de votre tournée",
    message: "5 minutes pour passer en revue les 10 meilleures recherches. Cliquez pour commencer.",
  });
});

chrome.notifications.onClicked.addListener(async id => {
  chrome.notifications.clear(id);
  if (id === "tour-rappel") {
    const { sites = ["leboncoin"], topOnly = true } = await chrome.storage.local.get(["sites", "topOnly"]);
    const res = await tourStart(sites, topOnly);
    if (res.ok && res.url) { await chrome.storage.local.set({ current: res.label }); chrome.tabs.create({ url: res.url }); }
  } else if (id === "tour-fin") {
    const { botUrl } = await chrome.storage.local.get("botUrl");
    chrome.tabs.create({ url: botUrl || "http://127.0.0.1:8000/" });
  }
});

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (!msg) return;
  if (msg.type === "import") {
    withPort("/api/import", { source: msg.source, listings: msg.listings }).then(reply);
    return true;
  }
  if (msg.type === "ping") {
    withPort("/api/ping").then(reply);
    return true;
  }
  if (msg.type === "tour") {
    withPort("/api/tour").then(reply);
    return true;
  }
  if (msg.type === "next") {
    tourNext(msg.step || 1, msg.site).then(async res => {
      if (res.ok && res.finished) await finishTour(res);
      else if (res.ok) await chrome.storage.local.set({ current: res.label });
      reply(res);
    });
    return true;
  }
  if (msg.type === "start") {
    tourStart(msg.sites, msg.topOnly).then(async res => {
      if (res.ok && res.label) await chrome.storage.local.set({ current: res.label });
      reply(res);
    });
    return true;
  }
  if (msg.type === "reminder") {
    chrome.storage.local.set({ reminder: !!msg.on }).then(scheduleReminders).then(() => reply({ ok: true }));
    return true;
  }
});
