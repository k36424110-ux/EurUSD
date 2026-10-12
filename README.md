# EURUSD Engine Pro

Dashboard de recherche EUR/USD conçu pour un déploiement sur Render Free. Il fournit une analyse technique expérimentale multi-timeframe, des candidats d'Order Blocks/FVG, des balayages de liquidité, un plan indicatif et un backtest de référence.

## Fonctionnalités

- Tableau de bord responsive, conçu pour mobile.
- Source de bougies publiques Yahoo Finance pour `EURUSD=X` (source indicative, pas un flux FX centralisé ni un flux garanti sans délai).
- EMA20/50/200, ATR, structure récente, balayage et réintégration de niveaux.
- Détection heuristique de zones candidates Order Block et Fair Value Gap.
- États `LONG`, `SHORT` et `WAIT`; la règle ne propose une entrée que si le biais et un déclencheur M1 sont alignés.
- Plan de trade indicatif avec stop ATR et cible en R.
- Endpoint de backtest baseline EMA20/EMA50, avec coût en pips réglable et hypothèses documentées.
- Endpoints de santé et d'analyse JSON.

## Important — limites de cette première version

- Il s'agit d'un moteur de recherche expérimental, pas d'un système validé pour du trading réel.
- Les Order Blocks et FVG sont des définitions heuristiques et doivent être évalués par backtest; ce ne sont pas des vérités objectives.
- Les bougies Yahoo peuvent être retardées, indisponibles ou différentes du flux du courtier. Si les données ne sont pas disponibles, l'API renvoie une erreur plutôt que de fabriquer des prix.
- Aucun calendrier macroéconomique, taux obligataire, flux CVD/footprint, carnet d'ordres, exécution d'ordres ou spread de courtier n'est intégré dans cette version.
- Le VWAP n'est affiché que si un volume exploitable existe. Le volume FX Yahoo est souvent absent ou nul.
- Le backtest fourni est une baseline de croisement EMA, pas un backtest de la stratégie OB/FVG. Il ne remplace pas une validation hors échantillon, et ne modélise pas parfaitement spread variable, slippage, commissions, latence et rejets d'ordres.
- Ne pas utiliser le résultat de backtest comme promesse de performance future.

## Déploiement gratuit GitHub + Render

1. Télécharge et décompresse l'archive.
2. Crée un nouveau dépôt GitHub et importe tous les fichiers de ce dossier.
3. Dans Render, choisis **New + → Blueprint** et sélectionne le dépôt contenant `render.yaml`, ou crée un **Web Service** avec :
   - Runtime: Python
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app --workers 1 --threads 4 --timeout 60`
4. Attends le déploiement puis ouvre l'URL fournie par Render.

Le fichier `render.yaml` demande le plan gratuit. Les offres et limites gratuites des fournisseurs peuvent évoluer; le service Render Free peut se mettre en veille et la première requête après veille peut être lente.

## API

- `GET /api/health` — santé du processus web.
- `GET /api/analysis` — analyse multi-timeframe.
- `GET /api/backtest?days=30&cost_pips=1.2` — backtest de référence.

## Exécution locale

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python app.py
```

Puis ouvrir `http://127.0.0.1:5000`.

## Prochaines étapes recommandées

1. Ajouter des tests unitaires et des tests de données manquantes.
2. Comparer les données Yahoo aux bougies du courtier utilisé.
3. Construire un backtest événementiel dédié aux règles OB/FVG/sweep, sans look-ahead bias.
4. Ajouter un calendrier macro à source et horodatage vérifiables.
5. Mesurer l'espérance nette, le profit factor, le drawdown et la stabilité hors échantillon avant de considérer un déploiement réel.
