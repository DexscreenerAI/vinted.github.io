// Fenêtre de l'icône : état de la connexion au logiciel et de la page ouverte.
let botPort = null;
const $ = id => document.getElementById(id);

function pageText(s) {
  if (!s) return "ouvrez une recherche Leboncoin, Vinted ou eBay";
  if (s.error) return `<span class="ko">${s.error}</span>`;
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
