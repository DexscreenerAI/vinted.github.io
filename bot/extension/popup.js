// Fenêtre de l'icône : état de la connexion au logiciel et de la page ouverte.
const esc = t => String(t ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let botPort = null;
const $ = id => document.getElementById(id);

function pageText(s) {
  if (!s) return "ouvrez une recherche Leboncoin, Vinted ou eBay";
  if (s.error) return `<span class="ko">${esc(s.error)}</span>`;
  if (!s.detected) return s.links ? `<span class="ko">${s.links} annonce(s) vue(s), aucun prix lu</span> → copiez le diagnostic`
    : "aucune annonce détectée (faites défiler)";
  return `${s.read} analysée(s), ${s.matched} surveillée(s), <span class="ok">${s.good} bonne(s) affaire(s)</span>`;
}

async function tab() {
  const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
  return t;
}

async function toPage(msg) {
  const t = await tab();
  try { return await chrome.tabs.sendMessage(t.id, msg); } catch (e) { return null; }
}

chrome.runtime.sendMessage({ type: "ping" }, res => {
  botPort = res && res.ok ? res.port : null;
  $("bot").innerHTML = botPort ? `<span class="ok">connecté</span> (port ${botPort})`
    : '<span class="ko">non lancé</span> — ouvrez ChasseurAffaires.exe';
});
toPage({ type: "stats" }).then(s => { $("page").innerHTML = pageText(s); });

$("scan").onclick = async () => {
  $("msg").textContent = "Analyse…";
  const s = await toPage({ type: "scan" });
  $("page").innerHTML = pageText(s);
  $("msg").textContent = s ? "" : "Cette page n'est pas une page Leboncoin, Vinted ou eBay (ou rechargez-la).";
};
$("open").onclick = () => chrome.tabs.create({ url: `http://127.0.0.1:${botPort || 8000}/` });
$("diag").onclick = async () => {
  const r = await toPage({ type: "diagnostic" });
  if (!r) { $("msg").textContent = "Ouvrez d'abord une page de recherche (puis rechargez-la)."; return; }
  await navigator.clipboard.writeText(r.text);
  $("msg").textContent = "Diagnostic copié : collez-le dans la conversation avec Claude.";
};

chrome.storage.local.get({ sites: ["leboncoin"], topOnly: true, reminder: true }, st => {
  $("sites").value = st.sites.join(",");
  $("top").checked = st.topOnly;
  $("reminder").checked = st.reminder;
});
chrome.runtime.sendMessage({ type: "tour" }, t => {
  $("cur").textContent = t && t.ok && t.active ? `${t.label} · ${t.good} bonne(s) affaire(s)` : "aucune en cours";
});
$("sites").onchange = () => chrome.storage.local.set({ sites: $("sites").value.split(",") });
$("top").onchange = () => chrome.storage.local.set({ topOnly: $("top").checked });
$("reminder").onchange = () => chrome.runtime.sendMessage({ type: "reminder", on: $("reminder").checked });
$("start").onclick = () => {
  const sites = $("sites").value.split(",");
  chrome.storage.local.set({ sites, topOnly: $("top").checked });
  chrome.runtime.sendMessage({ type: "start", sites, topOnly: $("top").checked }, async res => {
    if (!res || !res.ok) { $("msg").textContent = (res && res.error) || "Logiciel non lancé"; return; }
    const t = await tab();
    const onSite = t && /leboncoin\.fr|vinted\.fr|ebay\.(fr|de)/.test(t.url || "");
    if (onSite) chrome.tabs.update(t.id, { url: res.url }); else chrome.tabs.create({ url: res.url });
    window.close();
  });
};
