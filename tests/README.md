# Tests & garde-fous anti-régression (adision-api / Ad BUD)

Consolidation après 3 bugs prod du même type (un commit marche dans un contexte,
casse dans un autre, découvert en prod). Objectif : **attraper ces familles
AVANT la prod, automatiquement**.

## Volet 1 — Tests des chemins PDF critiques (AUTOMATIQUE via hook)

`tests/test_pdf_critical.py` — SANS prod, SANS DB (fake-DB + mocks HUB) :

| Test | Couvre |
|------|--------|
| `test_rapport_sans_client` | rapport projet sans client → 200, PDF non vide, snapshot |
| `test_rapport_avec_client` | branche logo client (gracieuse) |
| `test_rapport_vide` | projet sans lignes → rendu quand même |
| `test_rapport_jwt_none_ne_crash_pas` | **garde du 500 NameError 'authorization'** |
| `test_devis_rend` | devis → 200, non vide |
| `test_snapshot_complet_vs_filtre` | snapshot reflète la config (dissociation) |
| `test_emit_rapport_vers_hub` | émission HUB : post_report + snapshot complet |

Lancer :
```
python tests/test_pdf_critical.py      # autonome (sans pytest)
pytest tests/test_pdf_critical.py      # si pytest installé
```

**Preuve qu'ils protègent** : réintroduire le bug du 500 (appel inconditionnel
`_extract_bearer(authorization, token)` dans `_build_projet_report`) fait virer
les 4 tests rapport au ROUGE (NameError). Vérifié.

> VIU/TAK ont leur équivalent `tests/test_pdf_pages.py` (comptage multi-pages
> from-ad-hub) — même principe, prouvé rouge si on retire le fallback de comptage.

## Volet 3 — Fraîcheur de déploiement, TOUS les services (AUTOMATIQUE, 1 commande)

`tools/check_all_deploys.py` — compare git HEAD au commit **réellement
déployé** sur Railway pour chaque service (BUD, HUB, VIU, TAK, EST, CON, MAT…).
Évite de tester pendant un build (« pushé ≠ déployé »).
```
python tools/check_all_deploys.py       # backends Railway
python tools/check_vercel_deploys.py     # fronts Vercel
```
- `check_all_deploys.py` : exit 1 si un service Railway a `déployé != HEAD` ou
  `status != SUCCESS`. Cible le BON service par nom (un projet peut en héberger
  plusieurs, ex. adision-app = web/HUB + adision-est-api).
- `check_vercel_deploys.py` : pour chaque front Vercel, commit du dernier
  déploiement PROD READY (= servi) vs git HEAD du monorepo + état du dernier
  build. `[BUILD]` = en cours (NE PAS tester), `[X]` ERROR = live périmé,
  `[skip]` = build sauté (ignored-build-step, aucun fichier du front changé,
  bénin), `[behind]` = front non réaffecté. Réutilise le token du Vercel CLI.

## Volet 4 — Hook pré-push (AUTOMATIQUE après installation)

`hooks/pre-push` lance les tests critiques (et, côté VIU/TAK, la parité des
forks) et **bloque le push** si rouge. Installer une fois par repo :
```
git config core.hooksPath hooks
```

## Volet 2 — page_count côté HUB : décision (filet VIU/TAK suffit)

Non implémenté côté HUB, **volontairement** :
- L'upload HUB est en **URL présignée** (client → R2 direct) : le backend HUB ne
  voit jamais les bytes. Peupler `page_count` imposerait un **download R2 complet
  + pymupdf (nouvelle dép.) sur le chemin chaud de chaque confirm**.
- VIU/TAK **téléchargent déjà** le PDF pour le rendre/mesurer : y compter les
  pages est **gratuit** (zéro I/O en plus). « Compter là où on a les bytes. »
- Aucun consommateur n'a besoin de `page_count` sans aussi télécharger le PDF.
- Le bug réel est corrigé à la bonne couche (consommateur compte le vrai PDF,
  garde par les tests Volet 1).

Évolution possible (différée) : VIU/TAK pourraient *re-publier* le compte vers le
HUB (PATCH) après l'avoir calculé — cache à la source, sans download additionnel.

## Volet 5 — Dette notée : STAGING

Cause profonde des fausses régressions = **prod directe** (pas de staging). Les
volets 1-4 *mitigent* ; un environnement de **staging** *guérit*. Inscrit en
dette, NON amorcé.
