# Étude : Amazon (AliExpress → Amazon) et nouvelles sources d'articles (octobre 2026)

> Synthèse de 3 recherches. Les chiffres sont des ordres de grandeur, à vérifier sur les sites officiels (Seller Central, URSSAF, douane) avant de se lancer.

## 1. AliExpress → Amazon : le verdict

| Modèle | Capital | Marge nette réaliste | Verdict |
|---|---|---|---|
| Dropshipping AliExpress/Temu → client Amazon | quelques centaines d'€ | négative ou nulle | ❌ **Interdit par Amazon** et le compte est suspendu |
| FBA marque propre (Alibaba en gros → entrepôts Amazon) | 5 000-6 000 € | 0-10 % au départ, souvent négatif au lancement | ⚠️ Seulement avec de l'argent qu'on peut perdre |
| Arbitrage en ligne ou en magasin (promos → Amazon FBA) | 500-1 500 € | 10-25 % de retour sur investissement par article | ✅ Le seul point d'entrée raisonnable |
| Grossiste (marques établies) | 5 000-10 000 € | 8-15 % | Plus tard |

**Pourquoi le dropshipping ne marche pas :**
- **Règle Amazon** : il faut être le « vendeur officiel ». Il est interdit d'acheter chez un autre détaillant (AliExpress, Temu…) qui expédie directement au client.
- **Délais** : 10 à 20 jours depuis la Chine, alors que les indicateurs Amazon exigent moins de 4 % de colis en retard et plus de 95 % de colis suivis.
- **Taxes** : depuis le 1er juillet 2026, la franchise de droits sous 150 € a disparu, remplacée par un droit de 3 €. La France ajoute une taxe de 2 € par article sur les petits colis hors UE (depuis le 1er mars 2026).

**Exemple : produit acheté 4 € sur Alibaba, vendu 24,99 € sur Amazon (FBA, micro-entreprise)**

| Poste | Par unité |
|---|---|
| Achat | 4,00 € |
| Transport, douane, TVA à l'import (non récupérable en franchise) | 3,25 € |
| Commission Amazon 15 % | 3,75 € |
| Frais d'expédition FBA | 4,00 € |
| Publicité (≈ 15 % du chiffre d'affaires) | 3,75 € |
| Retours | 0,60 € |
| URSSAF 12,3 % | 3,07 € |
| Stockage | 0,15 € |
| **Reste** | **≈ 2,40 €, voire ≈ 0 € une fois payée la TVA sur les frais Amazon** |

Le « ×6 » des formations donne en réalité une marge de 0 à 10 %.

**Obligations à prévoir :**
- **GPSR** : une personne responsable dans l'UE, le marquage CE et les avertissements de sécurité.
- **REP** : identifiant unique ADEME exigé par Amazon.fr.
- **Abonnement Amazon Pro** : 39 € HT/mois.
- **Contrefaçon** : risque fréquent sur les produits AliExpress.

**Si on fait un bot Amazon plus tard :**
- **Données** : SP-API Amazon (gratuite avec un compte Pro : frais, prix, BSR), Keepa (≈ 49 €/mois, pour découvrir des produits), API AliExpress et CJdropshipping (gratuites, pour le coût fournisseur et le port).
- **Budget** : environ 90 €/mois.
- **Le manque à combler** : aucun outil existant ne relie automatiquement un produit Amazon à son coût rendu réel chez le fournisseur.

## 2. Nouvelles sources pour trouver des articles (bot Vinted)

| Source | Accès légitime | Intérêt |
|---|---|---|
| **eBay** | **API officielle Browse, gratuite** (compte développeur, 5 000 appels par jour) | ⭐⭐⭐ enchères qui finissent bientôt avec 0 à 2 offres, fautes d'orthographe (« carhart », « nintedo »), lots, ebay.fr, .de et .it |
| **Interenchères** | Alertes email gratuites par mot-clé | ⭐⭐⭐ lots de commissaires-priseurs, liquidations |
| Recherches eBay et Kleinanzeigen (Allemagne) | Alertes email | ⭐⭐ même système d'emails que Leboncoin |
| Drouot, Enchères du Domaine | Alertes à vérifier | ⭐⭐ |
| Vinted, Facebook Marketplace, ricardo.ch | Aucune voie officielle, sites protégés contre les robots | ❌ |

**Références de prix légales :**
- **Discogs** (vinyles) : gratuit.
- **BrickLink** (Lego) : gratuit, avec les prix vendus.
- **PriceCharting** (jeux vidéo, Pokémon) : payant.
- **eBay** : les prix vendus ne sont pas accessibles aux petits développeurs, mais on peut utiliser la médiane des annonces actives.
- **Cardmarket** : l'API est fermée aux nouveaux comptes.

## 3. Ordre conseillé
1. Lancer le bot Vinted avec les alertes Leboncoin par email (déjà prêt).
2. Ajouter **eBay** (API) et **Interenchères** (emails) comme nouvelles sources.
3. Amazon : commencer par l'arbitrage FBA avec 500 à 1 000 €, et ne construire le bot Amazon qu'une fois le compte Pro ouvert et les premiers chiffres réels en main.
