-- Ad BUD — QUATRE statuts de projet, un seul vocabulaire (9 septembre 2026).
--
-- Simon : « Les statuts sont maintenant : Projet en soumission, projet en
-- cours et projet archivé. Partout. par defaut. » Puis, devant la perte de la
-- distinction gagné/perdu qu'imposaient trois valeurs : « Ajouter un onglet
-- projet perdu. »
--
-- Le quatrième n'est pas un ornement. Sans lui, un projet PERDU deviendrait
-- « archivé » au même titre qu'un projet simplement rangé, et le taux de
-- réussite d'Ad ANA (ViewByClient, ADJUDICATION_OK) n'aurait plus rien à lire.
--
-- CORRESPONDANCE (les trois premières sont les règles explicites de Simon,
-- les autres en découlent) :
--     brouillon        -> en_soumission
--     adjuge           -> en_cours        « Adjugé et complet = projets en cours »
--     complet          -> en_cours        idem
--     perdu            -> perdu           « perdu = projets archivé » -> son propre onglet
--     archive          -> archive
--
-- `statut` et `categorie_affichage` parlent désormais la MÊME langue. Ils ne
-- sont pas fusionnés — l'un est le statut métier, l'autre le rangement visuel
-- glissable de la page Projets — mais leurs valeurs coïncident enfin.
--
-- Relevé avant écriture : 19 projets en `brouillon`, 1 en `archive`.
-- Aucun `adjuge`, `complet` ni `perdu` en base à ce jour — la correspondance
-- est écrite quand même, pour les projets créés avant ce jour dans d'autres
-- environnements et pour rendre la règle lisible.

-- 1. LES CHECKS D'ABORD : sans ça l'UPDATE serait refusé par l'ancienne
--    contrainte, qui ne connaît ni « en_soumission » ni « perdu ».
--
--    INCIDENT DU 9 SEPTEMBRE 2026, 17 h — cette migration a fait tomber
--    api-bud.adision.ca (502, « Connexion au serveur impossible… » dans
--    Ad BUD), pour la MÊME raison que sa jumelle 175 côté Ad HUB une heure
--    plus tôt. Le détail qui a coûté les deux fois :
--
--    la contrainte sur `statut` s'appelle `projet_statut_check` — au
--    SINGULIER, alors que la table s'appelle `projets`. Déclarée en ligne
--    dans le CREATE TABLE d'origine, Postgres l'a nommée d'après le nom que
--    la table portait ALORS ; la table a été renommée depuis, pas la
--    contrainte. Son nom n'est donc devinable NI par la convention
--    (`_chk` vs `_check`) NI par le nom actuel de la table.
--
--    L'étape 5 plus bas affirmait « il n'en avait AUCUN jusqu'ici ». C'était
--    faux, et le `IF EXISTS` a rendu l'erreur muette : le DROP ne trouvait
--    rien, ne levait rien, et l'UPDATE juste en dessous violait une
--    contrainte toujours vivante. Migration en erreur, `_bootstrap_db`
--    relève, l'API ne démarre pas du tout.
--
--    RÈGLE : avant de remplacer une contrainte, lire son vrai nom dans
--    pg_constraint. Jamais le déduire.
ALTER TABLE ad_budget.projets DROP CONSTRAINT IF EXISTS projets_categorie_affichage_chk;
ALTER TABLE ad_budget.projets DROP CONSTRAINT IF EXISTS projet_statut_check;
ALTER TABLE ad_budget.projets DROP CONSTRAINT IF EXISTS projets_statut_check;
ALTER TABLE ad_budget.projets DROP CONSTRAINT IF EXISTS projets_statut_chk;

-- 2. Les statuts.
UPDATE ad_budget.projets SET statut = 'en_soumission' WHERE statut = 'brouillon';
UPDATE ad_budget.projets SET statut = 'en_cours'      WHERE statut IN ('adjuge', 'complet');
-- 'perdu' et 'archive' gardent leur valeur : déjà dans le nouveau vocabulaire.

-- 3. Le rangement visuel accueille le quatrième onglet.
ALTER TABLE ad_budget.projets
  ADD CONSTRAINT projets_categorie_affichage_chk
  CHECK (categorie_affichage IN ('en_soumission', 'en_cours', 'perdu', 'archive'));

-- 4. LA VALEUR PAR DÉFAUT DE LA COLONNE, avant tout CHECK. Elle valait
--    'brouillon' : un projet créé sans statut explicite serait REFUSÉ par la
--    contrainte posée juste après, avec une erreur incompréhensible à
--    l'écran. C'est le genre de détail qui ne se voit qu'en production, à la
--    première création.
ALTER TABLE ad_budget.projets ALTER COLUMN statut SET DEFAULT 'en_soumission';

-- 5. Garde-fou sur le statut lui-même. Il en avait DÉJÀ un, `projet_statut_check`
--    (les cinq anciennes valeurs) — contrairement à ce que cette étape a
--    d'abord affirmé, et c'est précisément ce qui a fait tomber l'API : voir
--    l'incident décrit à l'étape 1, où les anciens noms sont maintenant
--    supprimés, AVANT les UPDATE.
ALTER TABLE ad_budget.projets
  ADD CONSTRAINT projets_statut_chk
  CHECK (statut IN ('en_soumission', 'en_cours', 'perdu', 'archive'));
