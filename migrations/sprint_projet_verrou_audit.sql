-- ════════════════════════════════════════════════════════════════════
-- Ad BUD — TRAÇABILITÉ du verrou projet + snapshot automatique au VERROUILLAGE
-- ════════════════════════════════════════════════════════════════════
-- Incident déclencheur (19-20 août 2026) : un projet fermé/verrouillé à
-- 3 005 198 $ avant taxes a été déverrouillé puis modifié (3 011 964 $) sans
-- qu'aucune trace ne survive — `verrouille_par`/`verrouille_le` (migration
-- sprint_projet_verrou) sont un MIROIR de l'état COURANT, écrasés à NULL au
-- déverrouillage. Rien ne dit qui a déverrouillé, quand, ni ce que le budget
-- était exactement au moment du verrou.
--
-- Deux ajouts, INDÉPENDANTS l'un de l'autre :
--
-- 1. `ad_budget.projet_verrou_log` — JOURNAL INSERT-ONLY (jamais d'UPDATE ni
--    de DELETE applicatif) de CHAQUE bascule verrou/déverrou que CE service
--    exécute lui-même (proxifiée vers le hub ou en fallback local). Survit à
--    tous les cycles verrou/déverrou suivants — contrairement au miroir
--    `verrouille_par`/`verrouille_le`. C'est le complément LOCAL du journal
--    autoritatif côté Ad HUB (`app_hub.project_verrou_log`, source unique
--    pour un projet lié) : celui-ci couvre aussi les projets AUTONOMES
--    (`ad_hub_project_id IS NULL`) qui n'ont pas de miroir hub.
--
-- 2. `snapshot_id` sur ce journal — pointe vers `app_ana.project_snapshots`
--    (table Sprint B existante, jusqu'ici déclenchée uniquement au passage
--    à un statut définitif). Le VERROUILLAGE devient un second déclencheur :
--    chaque `is_verrouille = TRUE` fige désormais l'état COMPLET du budget
--    (toutes les lignes, quantités, coûts, agrégats mat/mo/st, total) dans
--    un nouveau snapshot, `trigger_event = 'verrouillage'`. Les anciens
--    snapshots ne sont JAMAIS purgés par ce chemin : is_latest bascule,
--    la ligne reste consultable.
--
-- Additif + idempotent (CREATE TABLE IF NOT EXISTS). Ne touche aucune
-- colonne existante. Réversibilité :
--   DROP TABLE ad_budget.projet_verrou_log;
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ad_budget.projet_verrou_log (
  id              BIGSERIAL PRIMARY KEY,
  projet_id       INTEGER     NOT NULL,
  action          TEXT        NOT NULL CHECK (action IN ('verrouille', 'deverrouille')),
  acteur_email    TEXT,
  acteur_user_id  INTEGER,
  via_hub         BOOLEAN     NOT NULL DEFAULT FALSE,
  snapshot_id     INTEGER     NULL REFERENCES app_ana.project_snapshots(id) ON DELETE SET NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_projet_verrou_log_projet
  ON ad_budget.projet_verrou_log (projet_id, created_at DESC);
