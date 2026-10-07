# Étude : niches d'arbitrage Leboncoin → Vinted (octobre 2026)

> Synthèse de 3 recherches (niches, économie/fiscalité, faisabilité technique).
> ⚠️ Les chiffres par niche viennent surtout de blogs d'éditeurs d'outils de revente (Margeo, Fripio) : ce sont des **ordres de grandeur** à valider sur les ventes réelles.

## 1. Pourquoi l'écart de prix existe
- Vide-dressing / débarras / successions vendus **au lot** pour aller vite.
- Vendeurs qui ignorent la valeur d'une marque ou d'une époque (étiquette Carhartt 90s, classeur Pokémon WOTC).
- Annonces mal titrées (« veste marron travail ») → invisibles pour les acheteurs.
- LBC = marché local (main propre) ; Vinted = demande nationale/européenne.
- Vinted : 0 % de commission vendeur (l'acheteur paie protection + port).

## 2. Classement des niches (débutant, petit capital)

| # | Niche | Exemples achat → revente | Rotation | Risque principal | Détection bot |
|---|---|---|---|---|---|
| 1 | **Workwear / streetwear vintage homme** (Carhartt, Ralph Lauren, Levi's 501, The North Face) | Detroit jacket 30-50 € → 80-120 € ; Nuptse 700 40-65 € → 95-140 € | 10-25 j | Contrefaçons TNF/RL, tailles | ⭐⭐⭐ marque + modèle + prix |
| 2 | **Lots vêtements enfant de marque** (Petit Bateau, Jacadi, Bonpoint) | Lot 5 bodies 8-12 € → 28-38 € | 5-14 j | Petits paniers, temps photo | ⭐⭐⭐ « lot » + marque + taille |
| 3 | **Rétro gaming en packs** | Pack SNES + 15 jeux 120 € → 400-700 € | Moyenne | Matériel à tester, ticket 200-800 € | ⭐⭐⭐ console + « lot jeux » |
| 4 | **Lots / classeurs Pokémon anciens** | 18 € de boosters → 47 € en singles | Rapide (Cardmarket) | Bonnes cartes déjà retirées, fakes | ⭐⭐ « classeur », « wizards », « 1ère édition » |
| 5 | **Sneakers à la pièce** (Dunk, AF1, New Balance) | Dunk Low 35-55 € → 85-130 € | 3-14 j | Fakes, forte concurrence de bots | ⭐⭐ modèle + pointure |

**À éviter au départ** : luxe (capital ~2 000 €, contrefaçon), petite électronique (pannes, retours), Lego en vrac (≈4 h de tri / lot, meilleure sortie = BrickLink), vinyles (90 % valent 1-5 €, meilleure sortie = Discogs).

## 3. Stratégie « lot splitting »
- Acheter un lot de 10-40 pièces sur LBC, idéalement **en main propre** (vérification + pas de port).
- Coût unitaire = prix du lot ÷ pièces **vendables**.
- Les **5 meilleures pièces** doivent rembourser le lot + les frais.
- Viser une revente prudente ≥ **3×** le coût unitaire.
- Prévoir **20-35 % d'invendus** (revendus ensuite en mini-lots ou au kilo).

## 4. Économie réelle (exemple)
Acheté 10 € sur LBC, vendu 35 € sur Vinted, en micro-entreprise (franchise de TVA, versement libératoire) :

| | Livraison | Main propre |
|---|---|---|
| Coût de revient (achat + protection LBC + port) | 14,70 € | 10,00 € |
| URSSAF 12,3 % + formation 0,1 % + impôt 1 % + emballage | ≈5,20 € | ≈5,20 € |
| **Gain net** | **≈15,10 €** | **≈19,80 €** |

- Frais Vinted : 0 % côté vendeur ; protection acheteur 0,70 € + 5 % (payée par l'acheteur) ; envoi 2,50-3,90 € (Vinted Go, Mondial Relay, Relais Colis).
- Achat LBC sécurisé : 0,70 € + 5 % + port ; main propre en espèces = 0 € mais sans protection.

## 5. Cadre légal ⚠️
- **Acheter pour revendre = activité commerciale**, quel que soit le volume → statut (micro-entreprise) + **compte Vinted Pro** (gratuit, SIRET). Sinon : risque de blocage du compte.
- **DAC7** : Vinted déclare au fisc dès 30 ventes **ou** 2 000 €/an.
- Micro-entreprise vente de marchandises : 12,3 % du CA (9,3 % avec ACRE la 1re année) ; franchise de TVA jusqu'à 85 000 € ; au-delà, TVA sur la marge (art. 297 A CGI).
- Revente d'objets d'occasion achetés à des particuliers : en principe **registre des objets mobiliers** (« livre de police ») et déclaration en préfecture. À vérifier.
- En tant que Pro : rétractation 14 jours + garantie de conformité envers les acheteurs.

## 6. Le bot : faisabilité et architecture recommandée
**Constat** : aucun outil grand public ne fait le croisement LBC → Vinted (les outils existants, comme V-Tools, Monitorius ou Turtle Resell, ne surveillent que Vinted). Il y a donc une place à prendre.

**Contraintes** :
- Leboncoin n'a **pas d'API publique** et est protégé par DataDome. Leboncoin a déjà gagné en justice contre du scraping (droit *sui generis* des bases de données, TJ Paris 2017 confirmé en appel). → **Ne pas scraper la recherche LBC.**
- Vinted n'a pas d'API publique non plus : un endpoint interne `/api/v2/catalog/items` existe mais il est fragile (routes qui changent, soft-ban 429/403). Ses CGU interdisent l'extraction automatisée.

**MVP à faible risque** :
1. **Source LBC = alertes natives** (recherches sauvegardées par marque/catégorie, max ~25) → reçues dans une boîte mail dédiée, lue en IMAP, qui en extrait titre, prix, lien et vignette.
2. **Cote Vinted** : table de référence par marque + catégorie (+ taille), rafraîchie peu souvent (toutes les 24-72 h) et mise en cache ; médiane élaguée des prix ; références ignorées si moins de 10 comparables. Elle peut aussi être remplie à la main au départ.
3. **Normalisation** : un LLM extrait marque, modèle, taille et état du titre ; la vision sur la photo confirme la marque (logo, étiquette).
4. **Score** : `revente estimée = médiane × 0,85 − envoi/emballage` ; alerte si `revente / (prix LBC + frais) ≥ 2,5`.
5. **Notification** : Telegram ou Discord (photo, lien, ratio, comparables). **L'achat reste manuel.**
6. **Stack** : Python + SQLite + cron, sur un petit VPS ou Railway.

**À ne pas automatiser** : achat automatique (autocop), messages en masse aux vendeurs, bots connectés à vos comptes, contournement de DataDome (proxys rotatifs, solveurs), revente des données collectées.

## 7. Prochaines étapes suggérées
1. Créer la micro-entreprise + compte Vinted Pro.
2. Démarrer à la main sur la niche n°1 avec 10-20 alertes LBC ciblées (ex. « carhartt detroit », « levis 501 lot », « ralph lauren lot », « north face nuptse »).
3. Noter dans un tableau chaque achat et chaque vente → cette table devient la cote du bot.
4. Coder le MVP (lecteur IMAP → score → Telegram).

## Sources principales
- Niches : margeoapp.com/blog/top-categories-vendues-vinted-france-2026 · margeoapp.com/blog/achat-revente-leboncoin-guide · fripio.app/blog/taux-rotation-sell-through-vinted-2026 · margeoapp.com/niches/revente-jeux-video-retro · margeoapp.com/niches/revente-cartes-pokemon-tcg-2026
- Frais/fiscalité : fripio.app/blog/frais-protection-acheteur-vinted-2026 · vinted.fr/pro · economie.gouv.fr (charges micro-entreprise) · bofip.impots.gouv.fr (BOI-RES-TVA-000270-20260819)
- Technique/droit : github.com/etienne-hd/lbc · github.com/herissondev/vinted-api-wrapper · cms.law (arrêt Leboncoin, droit sui generis) · friptadium.com/blogs/actualites/bot-vinted
