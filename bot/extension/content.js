// Lit les annonces de la page ouverte (aucune requête vers le site), les envoie au logiciel
// et affiche un badge de rentabilité sur chacune. Relancé quand la page charge plus d'annonces.
(() => {
  const h = location.hostname;
  const source = h.includes("leboncoin") ? "leboncoin" : h.includes("vinted") ? "vinted" : "ebay";
  const pattern = source === "leboncoin" ? /leboncoin\.fr\/(ad\/|vi\/|[a-z_]+\/\d{8,})/
    : source === "vinted" ? /\/items\/\d+/ : /\/itm\/\d+/;
  const isSearch = () => /\/recherche|\/c\/|\/catalog|\/sch\/|\/b\//.test(location.pathname + location.search);
  const esc = t => String(t ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const sent = new Map();   // url -> élément carte
  const results = {};       // url -> évaluation renvoyée par le logiciel
  const stats = { links: 0, detected: 0, read: 0, matched: 0, good: 0, error: "", best: null };
  let timer = null, panel = null;

  // Titre de l'annonce. Sur Leboncoin le lien s'appelle « Voir l'annonce » : le vrai titre est sur
  // <article aria-label>, sur [data-qa-id=aditem_title] ou dans « Voir l'annonce: <titre> ».
  const GENERIC = /^(voir( l[’']annonce)?|see|ansehen|zur anzeige)\s*$/i;
  function titleOf(a, box, text) {
    const clean = t => (t || "").replace(/^voir l[’']annonce\s*:\s*/i, "").split(/,\s*[\d \u00a0.,]+\s?€/)[0].trim();
    const scope = box || a;
    const titled = scope.querySelector("[data-qa-id='aditem_title'], [data-test-id='adcard-title'], [data-testid$='--description-title']");
    const article = a.closest("article");
    const inner = a.querySelector("[title]");
    const lines = text.split("\n").map(x => x.trim()).filter(x => x.length > 3 && !/€/.test(x) && !GENERIC.test(x));
    const candidates = [article && article.getAttribute("aria-label"), titled && titled.innerText,
      a.getAttribute("title"), inner && inner.getAttribute("title"), a.getAttribute("aria-label"),
      lines.sort((x, y) => y.length - x.length)[0]];
    for (const c of candidates) {
      const t = clean(c);
      if (t.length > 2 && !GENERIC.test(t)) return t;
    }
    return "";
  }

  function extract() {
    const fresh = [];
    let links = 0;
    for (const a of document.querySelectorAll("a[href]")) {
      const url = a.href.split("?")[0].split("#")[0];
      if (!pattern.test(url)) continue;
      links++;
      if (sent.has(url)) continue;
      let box = a, text = "";
      for (let i = 0; i < 7 && box; i++) {
        text = box.innerText || "";
        if (/\d\s?(€|EUR\b)/.test(text) || /\d\s?€/.test(a.title || "") || /\d\s?€/.test(a.getAttribute("aria-label") || "")) break;
        box = box.parentElement;
      }
      const all = (a.getAttribute("title") || "") + "\n" + (a.getAttribute("aria-label") || "") + "\n" + text;
      // Prix : d'abord l'élément marqué « prix » par le site, sinon le texte de la carte.
      // Les milliers doivent faire 3 chiffres (« 1 200 € ») : « Taille 42 30 € » donne 30 €, pas 4230 €.
      const priceEl = (box || a).querySelector("[data-test-id='price'], [data-qa-id='aditem_price'], [data-testid$='--price-text'], [data-testid*='price'], .s-item__price");
      const PRICE_RE = /(\d{1,3}(?:[ \u00a0\u202f.]\d{3})+|\d+)(?:,(\d{1,2}))?\s?(?:€|EUR\b)/;
      const m = (priceEl && (priceEl.innerText || priceEl.textContent || '').match(PRICE_RE)) || all.match(PRICE_RE);
      if (!m) continue;
      const price = parseFloat(m[1].replace(/[ \u00a0\u202f.]/g, '') + '.' + (m[2] || '0'));
      const title = titleOf(a, box, text);
      if (!title) continue;
      const img = (box || a).querySelector("img");
      sent.set(url, box || a);
      fresh.push({ title, price, url, image: img ? (img.currentSrc || img.src) : "" });
    }
    stats.links = links;
    return fresh;
  }

  function badge(url, d) {
    const card = sent.get(url);
    if (!card || card.querySelector(":scope > .chasseur-badge")) return;
    const b = document.createElement("div");
    b.className = "chasseur-badge";
    const lu = `${Math.round(d.price)} €`;  // prix lu sur la page : permet de repérer une erreur de lecture
    b.textContent = (d.good ? `🔥 ${lu} → x${d.ratio.toFixed(1)} · +${Math.round(d.net_profit)} €` : `${lu} → x${d.ratio.toFixed(1)}`)
      + (d.ai_pending ? " · 🤖 analyse…" : d.ai_verdict === "passer" ? " · 🤖 écartée par Claude"
        : d.ai_verdict === "acheter" ? " · 🤖 validée" : d.ai_verdict === "a_verifier" ? " · 🤖 à vérifier" : "");
    b.title = `Prix lu : ${d.price} € · ${d.rule} — revente ≈ ${Math.round(d.est_resale)} €, coût ${Math.round(d.buy_cost)} €, bénéfice net ≈ ${Math.round(d.net_profit)} €`;
    Object.assign(b.style, {
      position: "absolute", top: "6px", left: "6px", zIndex: 5, padding: "3px 8px", borderRadius: "999px",
      font: "600 12px system-ui, sans-serif", color: "#fff", background: d.good ? "#0a7f6f" : "#8a8a84",
      boxShadow: "0 1px 4px rgba(0,0,0,.25)", pointerEvents: "auto",
    });
    if (getComputedStyle(card).position === "static") card.style.position = "relative";
    card.appendChild(b);
  }

  function render() {
    if (!isSearch() && !stats.detected) return;
    if (!panel) {
      panel = document.createElement("div");
      Object.assign(panel.style, {
        position: "fixed", right: "16px", bottom: "16px", zIndex: 2147483647, width: "300px",
        padding: "10px 14px", borderRadius: "12px", font: "13px/1.4 system-ui, sans-serif",
        background: "#fff", color: "#1d1d1b", boxShadow: "0 6px 20px rgba(0,0,0,.2)",
      });
      panel.innerHTML = `<div style="display:flex;align-items:center;gap:6px"><b style="flex:1">Chasseur d'affaires</b>
          <span data-act="close" title="Masquer" style="cursor:pointer;padding:0 4px;color:#6b6b66">✕</span></div>
        <div data-zone="info"></div>
        <div data-zone="search" style="margin-top:6px;color:#6b6b66;font-size:12px"></div>
        <div style="display:flex;gap:6px;margin-top:8px">
          <button data-act="prev" title="Recherche précédente" style="${BTN}">◀</button>
          <button data-act="next" style="${BTN};flex:1;background:#0a7f6f;border-color:#0a7f6f;color:#fff;font-weight:600">Recherche suivante ▶</button>
        </div>`;
      panel.addEventListener("click", e => {
        const act = e.target.closest("[data-act]")?.dataset.act;
        if (act === "close") panel.style.display = "none";
        if (act === "next" || act === "prev") go(act === "next" ? 1 : -1);
      });
      document.body.appendChild(panel);
      chrome.runtime.sendMessage({ type: "tour" }, t => {
        const z = panel.querySelector("[data-zone=search]");
        if (t && t.ok && t.active) z.textContent = `${t.label} · ${t.good} bonne(s) affaire(s) depuis le début · Alt+Maj+→`;
        else z.textContent = "Astuce : « Recherche suivante » lance une tournée des 10 meilleures recherches.";
      });
    }
    let html = "";
    if (stats.error) html += `⚠️ ${esc(stats.error)}`;
    else if (!stats.detected) html += stats.links
      ? `${stats.links} annonce(s) vue(s) mais aucun prix lu. Icône de l'extension → « Copier le diagnostic ».`
      : "Aucune annonce détectée pour l'instant (faites défiler la page).";
    else html += `${stats.read} annonce(s) analysée(s) · ${stats.matched} surveillée(s) · <b>${stats.good} bonne(s) affaire(s)</b>`
      + (stats.best ? `<br>🔥 ${esc(stats.best.title.slice(0, 40))} : x${stats.best.ratio.toFixed(1)}, +${Math.round(stats.best.net_profit)} €` : "")
      + (stats.matched ? "" : "<br><small>Aucune ne correspond aux articles surveillés.</small>");
    panel.style.border = "2px solid " + (stats.error ? "#b4540a" : "#0a7f6f");
    const zone = panel.querySelector("[data-zone=info]");
    if (zone.innerHTML !== html) zone.innerHTML = html;
  }

  const BTN = "font:inherit;padding:6px 10px;border-radius:8px;border:1px solid #d0d0ca;background:#f6f6f3;color:#1d1d1b;cursor:pointer";

  // Un clic (ou Alt+Maj+→) = l'utilisateur ouvre la recherche suivante de sa tournée
  function go(step) {
    chrome.runtime.sendMessage({ type: "next", step, site: source }, res => {
      if (!res || !res.ok) { stats.error = (res && res.error) || "Logiciel non lancé"; render(); return; }
      if (res.finished) { showTour(`✅ Tournée terminée : <b>${res.good} bonne(s) affaire(s)</b> · `
        + `<a href="http://127.0.0.1:${res.port || 8000}/" target="_blank" style="color:#0a7f6f">Voir</a>`); return; }
      location.href = res.url;
    });
  }

  function showTour(html) {
    if (!panel) render();
    const z = panel && panel.querySelector("[data-zone=search]");
    if (z) z.innerHTML = html;
  }

  function scan(done) {
    const listings = extract();
    stats.detected += listings.length;
    if (!listings.length) { render(); if (done) done(); return; }
    chrome.runtime.sendMessage({ type: "import", source, listings }, res => {
      if (!res || !res.ok) {
        for (const l of listings) sent.delete(l.url); // on réessaiera
        stats.detected -= listings.length;
        stats.error = (res && res.error) || "Logiciel Chasseur d'affaires non lancé";
        retryDelay = Math.min(retryDelay * 2, 60000);
      } else {
        stats.error = "";
        retryDelay = 1200;
        stats.read += res.received; stats.matched += res.matched; stats.good += res.good;
        for (const d of res.deals) { results[d.url] = d; badge(d.url, d); }
        stats.best = Object.values(results).filter(d => d.good).sort((a, b) => b.net_profit - a.net_profit)[0] || null;
      }
      render();
      if (done) done();
    });
  }

  function diagnostic() {
    const cards = [];
    for (const a of document.querySelectorAll("a[href]")) {
      if (!pattern.test(a.href)) continue;
      let box = a;
      for (let i = 0; i < 4 && box.parentElement; i++) box = box.parentElement;
      cards.push(box.outerHTML.replace(/\s+/g, " ").slice(0, 2500));
      if (cards.length >= 2) break;
    }
    const sample = [...document.querySelectorAll("a[href]")].map(a => a.href).filter(u => u.includes(location.hostname)).slice(0, 40);
    return JSON.stringify({ url: location.href, stats, sample, cards }, null, 1);
  }

  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg.type === "stats") reply({ source, url: location.href, ...stats });
    else if (msg.type === "scan") { sent.clear(); Object.assign(stats, { detected: 0, read: 0, matched: 0, good: 0 }); scan(() => reply({ source, ...stats })); return true; }
    else if (msg.type === "diagnostic") reply({ text: diagnostic() });
  });

  scan();
  let retryDelay = 1200;
  new MutationObserver(muts => {
    const ours = n => n === panel || (panel && panel.contains(n)) || (n.classList && n.classList.contains("chasseur-badge"));
    if (muts.every(m => ours(m.target) || [...m.addedNodes].every(ours))) return;
    clearTimeout(timer); timer = setTimeout(() => scan(), stats.error ? retryDelay : 1200);
  }).observe(document.body, { childList: true, subtree: true });
})();
