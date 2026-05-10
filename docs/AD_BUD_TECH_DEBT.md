# Ad BUD - Dette technique

Tracker des problemes structurels connus, avec leur severite et un
plan de fix. A revoir periodiquement.

---

## #1. Duplicate users dans `ad_budget.users` — severite MOYEN

### Symptome

`_provision_user` (`modules/auth_jwt.py:64`) lookup par email sans
contrainte UNIQUE :
```sql
SELECT id, nom, email, role, created_at
FROM ad_budget.users WHERE LOWER(email) = %s
```

Si plusieurs rows existent pour le meme email (race a l'auto-provision,
seed manuel, migration historique), `cur.fetchone()` retourne une row
non-deterministe. Resultat : `user["id"]` peut varier entre requetes
pour le meme JWT.

Consequence observee (mai 2026) : projets crees via
`/projects/from-viu-v2 mode=new` se retrouvent avec un `user_id` qui
ne matche plus la row "canonique" actuelle de `ad_budget.users`.
Les endpoints `get_projet` et `get_projets` faisaient INNER JOIN
sur `ad_budget.users`, excluant silencieusement ces projets
orphelins. Le user voyait un 404 sur le deep-link et une liste
amputee.

### Fix temporaire (deja deploye)

LEFT JOIN dans `get_projet` (ligne 1155) et `get_projets` (ligne 670).
Les projets orphelins sont retournes avec `user_nom = NULL`. Frontend
n'utilise pas `user_nom`, aucun impact UI. Masque le bug structurel
mais ne le corrige pas.

### Fix definitif (TODO)

1. **Migration** : ajouter `UNIQUE (LOWER(email))` sur `ad_budget.users`
   apres avoir merge les duplicates.
2. **Script de cleanup** : pour chaque email avec >1 row, garder la
   plus ancienne (ou celle avec le plus de projets), repointer les
   `ad_budget.projets.user_id` des autres rows vers la canonique,
   supprimer les rows orphelines.
3. **Garde-fou code** : `_provision_user` devrait `ORDER BY id ASC
   LIMIT 1` pour etre deterministe meme si la contrainte UNIQUE n'est
   pas encore en place.

### Pourquoi pas tout de suite

Necessite migration BD avec downtime potentiel ou script ad-hoc en
dehors du deploy auto. A planifier.

---

## #2. Endpoint `GET /budget/projets/{id}` non securise — severite ELEVE

### Symptome

```python
@router.get("/projets/{projet_id}")
def get_projet(projet_id: int):
    # PAS de user=Depends(jwt_user)
    # PAS de check projet.user_id == user.id
    conn = get_conn()
    cur = conn.cursor(row_factory=dict_row)
    cur.execute("SELECT p.*, u.nom as user_nom FROM ... WHERE p.id = %s", ...)
```

N'importe qui ayant un JWT valide pour Ad BUD (toutes equipes
confondues) peut acceder a n'importe quel projet par ID en iterant
les ids. Vraie faille de leak de donnees clients.

D'autres endpoints `/projets/{id}/...` (export, pdf, snapshots,
lignes) ont peut-etre le meme probleme — a auditer.

### Fix definitif (TODO)

1. Ajouter `user=Depends(jwt_user)` a `get_projet` et toutes les routes
   `/projets/{id}/*`.
2. Apres le SELECT, verifier `row["user_id"] == user["id"]` ; sinon
   raise `HTTPException(404)` (pas 403, pour ne pas leaker
   l'existence d'un projet appartenant a quelqu'un d'autre).
3. Cas admin : si `user["role"] == "admin"`, autoriser tous les
   projets.

### Pourquoi pas tout de suite

Avant cette session, pas de donnees clients sensibles en BD (que des
tests). Mais devient urgent des qu'un vrai client est onboarde.
A traiter en priorite **avant** le premier projet client reel.

---

## #3. Notations d'unites divergentes Ad VIU v2 vs Ad BUD — severite MOYEN

### Symptome

Ad VIU v2 utilise des notations ASCII plain (decision produit J7
"Unites cibles HARDCODEES, pas d'exposants Unicode") :
`pi2`, `m2`, `m3`, `un`, `pl`, `ml`.

Ad BUD frontend (`apps/ad-bud/src/App.jsx:18` constante `allowedUnites`)
utilise des notations avec exposants Unicode + libelles longs :
`pi²`, `m²`, `m³`, `unité`, `plin`, `mlin`, plus `pi`, `global`,
`sem`, `/1000$`.

Quand un projet est cree via Ad VIU v2 push (mode=new ou existing),
les items v2 sont inseres avec leurs notations ASCII. Le `<select>`
Ad BUD ne reconnait pas ces valeurs (pas dans `allowedUnites`) et
n'affiche rien dans la cellule unite. L'estimateur voit "items
sans unites".

### Fix temporaire (deja deploye)

Mapping cote backend dans `/projects/from-viu-v2` : `_map_v2_unite_to_bud`
convertit les notations ASCII vers Unicode/libelles longs avant
l'INSERT. Table de correspondance dans `_VIU_V2_TO_BUD_UNITE`.

Ad VIU v2 garde sa convention propre (no Unicode, conforme J7).
Ad BUD garde la sienne. Le mapping vit dans la couche d'integration.

### Fix definitif (TODO)

Standardize cross-module. 2 options :

**(A) Aligner Ad VIU v2 sur Ad BUD** : sortir directement `pi²`, `m²`,
`unité`, `plin`, etc. Casse la decision J7 (no Unicode), peut affecter
le rendering Ad VIU v2 si certains contextes ne supportent pas
l'Unicode. Faible effort backend, impact UI a auditer.

**(B) Aligner Ad BUD sur Ad VIU v2** (no Unicode). Migration BD
massive sur `ad_budget.budget_lignes` existantes, refonte du
dropdown frontend, refactor potentiel des exports PDF/Excel qui
peuvent rendre `pi²` differemment. Effort eleve.

**(C) Standard externe** : adopter une convention industrie-construction
existante (CSI / ASTM). Probablement le plus rigoureux mais demande
recherche prealable.

### Pourquoi pas tout de suite

Le mapping temporaire fait illusion fonctionnellement. Standardize
demande une decision produit (Unicode vs ASCII) qui n'est pas
urgente. A revisiter quand on rationalise les conventions de
notation a l'echelle de la suite Adision.
