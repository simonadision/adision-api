-- ════════════════════════════════════════════════════════════════════
-- Ad BUD — JOURNAL DES PARAMÈTRES FINANCIERS
-- ════════════════════════════════════════════════════════════════════
-- Simon, 7 oct. 2026 07 h 03 : « go journal ».
--
-- POURQUOI. Le 6 octobre, le projet 290 portait 7,887 % / 7,89 %
-- d'administration et profit au lieu des 8 % que Simon avait mis. Enquête des
-- quatre postes, journaux Railway relus sur toute leur rétention (8 jours) :
-- aucune trace — la valeur était plus ancienne, et la base ne gardait AUCUN
-- historique de ces champs. On a dû conclure « on ne saura pas ». Une
-- question d'argent ne doit plus jamais finir comme ça.
--
-- CE QUI EST JOURNALISÉ : chaque champ RÉELLEMENT changé parmi les
-- paramètres qui font le prix — les 16 pct_admin_* (discipline et
-- catégorie), pct_admin_mode, arrondi_dollar. Une ligne par champ : qui
-- (courriel), quand, valeur d'avant, valeur d'après. Écrit par
-- update_projet DANS LA MÊME TRANSACTION que la modification : un journal
-- qui pourrait manquer une écriture réussie ne prouverait rien.
--
-- Valeurs en TEXTE : le journal rend ce qui était stocké, sans le
-- reformater (NULL reste NULL = « hérite du % discipline » pour une
-- catégorie).
--
-- IDEMPOTENTE. Suivi par nom de fichier dans ad_budget.schema_migrations.
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ad_budget.journal_parametres_financiers (
    id         BIGSERIAL PRIMARY KEY,
    projet_id  INTEGER NOT NULL
                 REFERENCES ad_budget.projets(id) ON DELETE CASCADE,
    champ      TEXT NOT NULL,
    avant      TEXT,
    apres      TEXT,
    par        TEXT,
    le         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- UN GESTE = une requête (PC3, relecture du 7 oct.) : une saisie globale
    -- écrit 16 champs d'un coup ; sans identifiant commun, elle ressemblerait
    -- à 16 saisies séparées -- exactement l'information qui a manqué au 290
    -- (7,89 sur un champ, 7,887 sur trois : au moins DEUX gestes).
    geste      UUID
);

-- ⚠ ON DELETE CASCADE : supprimer RÉELLEMENT un projet efface son journal.
-- En pratique les projets passent par la corbeille (suppression douce), le
-- journal reste ; c'est dit ici pour ne pas le découvrir plus tard.

CREATE INDEX IF NOT EXISTS idx_journal_parametres_projet
    ON ad_budget.journal_parametres_financiers (projet_id, le DESC);

COMMENT ON TABLE ad_budget.journal_parametres_financiers IS
  'Qui a changé quel paramètre financier (A&P, mode, arrondi), quand, de quoi '
  'à quoi (7 oct 2026, après l''enquête 7,887 % du projet 290).';
