// Lit les annonces de la page ouverte (aucune requête vers le site), les envoie au logiciel
// et affiche un badge de rentabilité sur chacune. Relancé quand la page charge plus d'annonces.
(() => {
  const h = location.hostname;
  const source = h.includes("leboncoin") ? "leboncoin" : h.includes("vinted") ? "vinted" : "ebay";
  const pattern = source === "leboncoin" ? /leboncoin\.fr\/(ad\/|vi\/|[a-z_]+\/\d{8,})/
    : source === "vinted" ? /\/items\/\d+/ : /\/itm\/\d+/;
  const sent = new Map();   // url -> élément carte
  const results = {};       // url -> évaluation renvoyée par le logiciel
  const totals = { read: 0, matched: 0, good: 0 };
  let timer = null, panel = null;

  function extract() {
    const fresh = [];
    for (const a of document.querySelectorAll("a[href]")) {
      const url = a.href.split("?")[0].split("#")[0];
      if (!pattern.test(url) || sent.has(url)) continue;
      let box = a, text = "";
      for (let i = 0; i < 6 && box; i++) {
        text = box.innerText || "";
        if (/\d\s?€/.test(text) || /\d\s?€/.test(a.title || "")) break;
        box = box.parentElement;
      }
      const all = (a.getAttribute("title") || "") + "\n" + text;
      const m = all.match(/(\d[\d   .]*(?:,\d{1,2})?)\s?€/);
      if (!m) continue;
      const price = parseFloat(m[1].replace(/[   .]/g, "").replace(",", "."));
      const lines = text.split("\n").map(x => x.trim()).filter(x => x.length > 3 && !/€/.test(x));
      let title = a.getAttribute("title") || a.getAttribute("aria-label") || lines.sort((x, y) => y.length - x.length)[0] || "";
      title = title.split(/,\s*[\d  .,]+\s?€/)[0].trim();
      if (!title) continue;
      const img = (box || a).querySelector("img");
      sent.set(url, box || a);
      fresh.push({ title, price, url, image: img ? (img.currentSrc || img.src) : "" });
    }
    return fresh;
  }

  function badge(url, d) {
    const card = sent.get(url);
    if (!card || card.querySelector(":scope > .chasseur-badge")) return;
    const b = document.createElement("div");
    b.className = "chasseur-badge";
    b.textContent = d.good ? `🔥 x${d.ratio.toFixed(1)} · +${Math.round(d.net_profit)} €` : `x${d.ratio.toFixed(1)}`;
    b.title = `${d.rule} — revente ≈ ${Math.round(d.est_resale)} €, coût ${Math.round(d.buy_cost)} €, bénéfice net ≈ ${Math.round(d.net_profit)} €`;
    Object.assign(b.style, {
      position: "absolute", top: "6px", left: "6px", zIndex: 5, padding: "3px 8px", borderRadius: "999px",
      font: "600 12px system-ui, sans-serif", color: "#fff", background: d.good ? "#0a7f6f" : "#8a8a84",
      boxShadow: "0 1px 4px rgba(0,0,0,.25)", pointerEvents: "auto",
    });
    if (getComputedStyle(card).position === "static") card.style.position = "relative";
    card.appendChild(b);
  }

  function showPanel(text, ok) {
    if (!panel) {
      panel = document.createElement("div");
      Object.assign(panel.style, {
        position: "fixed", right: "16px", bottom: "16px", zIndex: 2147483647, maxWidth: "320px",
        padding: "10px 14px", borderRadius: "12px", font: "13px/1.4 system-ui, sans-serif",
        background: "#fff", color: "#1d1d1b", boxShadow: "0 6px 20px rgba(0,0,0,.2)", cursor: "pointer",
      });
      panel.addEventListener("click", () => { panel.style.display = "none"; });
      document.body.appendChild(panel);
    }
    panel.style.border = "2px solid " + (ok ? "#0a7f6f" : "#b4540a");
    panel.innerHTML = text;
  }

  function scan() {
    const listings = extract();
    if (!listings.length) return;
    chrome.runtime.sendMessage({ type: "import", source, listings }, res => {
      if (!res || !res.ok) {
        for (const l of listings) sent.delete(l.url); // on réessaiera
        showPanel("<b>Chasseur d'affaires</b><br>Logiciel non lancé : ouvrez ChasseurAffaires.exe.", false);
        return;
      }
      totals.read += res.received; totals.matched += res.matched; totals.good += res.good;
      for (const d of res.deals) { results[d.url] = d; badge(d.url, d); }
      const best = Object.values(results).filter(d => d.good).sort((a, b) => b.net_profit - a.net_profit)[0];
      showPanel(`<b>Chasseur d'affaires</b><br>${totals.read} annonce(s) analysée(s) · ${totals.matched} surveillée(s) · `
        + `<b>${totals.good} bonne(s) affaire(s)</b>`
        + (best ? `<br>🔥 ${best.title.slice(0, 40)} : x${best.ratio.toFixed(1)}, +${Math.round(best.net_profit)} €` : ""), true);
    });
  }

  scan();
  new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(scan, 1200); })
    .observe(document.body, { childList: true, subtree: true });
})();
