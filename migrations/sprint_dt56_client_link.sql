-- ════════════════════════════════════════════════════════════════════
-- DT-56 D1 — Lien Ad BUD ↔ app_central.clients
--
-- Ajoute une référence applicative depuis ad_budget.projets vers
-- app_central.clients.id (côté backend Ad HUB adision-app-api).
-- Pas de FK SQL cross-base (DBs séparées Railway).
--
-- Stratégie rétro-compat verrouillée :
--   - Les colonnes texte existantes (client, nom_client, contact_client,
--     email_client, telephone_client) sont CONSERVÉES intactes.
--   - Les anciens projets gardent leurs métadonnées texte libre.
--   - Les nouveaux projets renseignent client_id (FK applicative) et
--     peuvent dénormaliser le nom dans nom_client pour rétro-compat
--     côté lecture / PDF exports legacy.
--
-- Type BIGINT : aligné sur app_central.clients.id (BIGSERIAL).
-- ════════════════════════════════════════════════════════════════════

BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'ad_budget'
      AND table_name = 'projets'
      AND column_name = 'client_id'
  ) THEN
    ALTER TABLE ad_budget.projets
      ADD COLUMN client_id BIGINT;

    COMMENT ON COLUMN ad_budget.projets.client_id IS
      'Référence applicative vers app_central.clients.id (DB séparée). '
      'Pas de FK SQL cross-base. NULL = projet legacy ou client ad hoc '
      '(les colonnes nom_client/contact_client/etc. texte libre '
      'restent la source côté rétro-compat).';
  END IF;
END$$;

-- Index partiel : seuls les projets reliés à un client sont indexés.
-- Permet la requête inverse rapide « tous les projets de ce client ».
CREATE INDEX IF NOT EXISTS idx_projets_client_id
  ON ad_budget.projets (client_id)
  WHERE client_id IS NOT NULL;

COMMIT;
