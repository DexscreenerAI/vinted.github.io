# Détecteur de bonnes affaires Leboncoin → Vinted

Le bot **trouve** les annonces Leboncoin sous-cotées et vous les envoie sur Telegram, avec le multiplicateur et le bénéfice net estimés. **Il n'achète rien** : c'est vous qui décidez et qui contactez le vendeur.

Il ne fait **aucune requête sur Leboncoin**. Il lit seulement les **emails d'alerte** que Leboncoin vous envoie pour vos recherches sauvegardées. Il ne risque donc ni blocage ni problème de conditions d'utilisation.

```
Alertes Leboncoin (email) ──► lecture IMAP ──► règles + cote ──► calcul du multiplicateur ──► Telegram
```

## Installation

```bash
cd bot
pip install -r requirements.txt
cp config.example.yaml config.yaml   # vos niches et vos prix de revente
cp .env.example .env                 # accès email + Telegram
```

### 1. Créer les alertes sur Leboncoin
Avec un compte Leboncoin dont l'email est la **boîte dédiée** :
1. Faites une recherche ciblée, triée par « plus récentes » (ex. `carhartt detroit`, `lot ralph lauren`, `north face nuptse`, `lot petit bateau`, `snes`).
2. Cliquez sur « Sauvegarder la recherche » et activez les **alertes email**.

Une alerte par règle de `config.yaml` est un bon début.

### 2. Boîte mail
Gmail : activez la validation en 2 étapes, puis créez un **mot de passe d'application** et mettez-le dans `IMAP_PASSWORD`.

### 3. Telegram
1. Créez un bot avec **@BotFather** et récupérez son token.
2. Envoyez-lui un message.
3. Récupérez votre identifiant de chat avec **@userinfobot**.

Sans Telegram, les alertes s'affichent simplement dans le terminal.

## Utilisation

```bash
python -m finder run                    # traite les nouvelles alertes une fois
python -m finder run --loop 120         # tourne en continu (toutes les 2 min)
python -m finder check "Lot de 10 polos Ralph Lauren" 40              # évaluer une annonce à la main
python -m finder check "Doudoune North Face Nuptse" 45 --main-propre
python -m finder parse alerte.eml       # tester l'extraction sur un email sauvegardé
```

Exemple d'alerte :
```
🔥 Lot de 10 polos Ralph Lauren
Règle : Lot vêtements marque homme
Prix LBC : 40 € → coût d'achat 46.20 €
Revente estimée : 128 € (6 pièces vendables)
Multiplicateur : x2.8 · bénéfice net ≈ 61 €
```

## Comment le score est calculé

| | |
|---|---|
| Coût d'achat | prix + 0,70 € + 5 % + port (ou prix seul si `--main-propre` / `hand_delivery: true`) |
| Revente estimée | `ref_price` × 0,85 (les prix vendus sont plus bas que les prix affichés) × pièces vendables |
| Lots | nombre de pièces lu dans le titre (« lot de 12 »), × `sellable_rate` (65-75 % vendables) |
| Bénéfice net | revente − 13,4 % (URSSAF + formation + versement libératoire) − emballage − coût d'achat |
| Alerte si | multiplicateur ≥ `min_ratio` (2,5) **et** bénéfice net ≥ `min_profit` (15 €) **et** prix ≤ `max_buy` |

## Régler `config.yaml`
- `ref_price` est **le chiffre le plus important**. Au début, c'est une estimation (voir `docs/etude-niches-arbitrage.md`). Ensuite, mettez-y le prix médian de **vos ventes réelles**.
- `all`, `any` et `none` sont les mots-clés de la règle : tous doivent être présents, au moins un doit l'être, aucun ne doit l'être. Les accents et les majuscules sont ignorés.
- `exclude` élimine les annonces douteuses (« style carhartt », « replica », etc.).

## Lancer en continu
- Sur un petit VPS ou un Raspberry Pi : `python -m finder run --loop 120`, ou un cron toutes les 2 minutes.
- Les emails traités sont marqués comme lus, et les annonces déjà vues sont mémorisées dans `finder.db`.

## Tests
```bash
python -m unittest discover -s tests
```
