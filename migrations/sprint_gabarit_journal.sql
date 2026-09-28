-- ════════════════════════════════════════════════════════════════════════
-- Ad BUD — journal des modifications de gabarits, PAR UTILISATEUR
--
-- CONTEXTE (28 sept 2026)
--   Question : deux personnes modifient-elles le même gabarit en même
--   temps ? (risque résiduel de l'enregistrement automatique, #703 du
--   monorepo). Les journaux HTTP de Railway ne gardent que l'IP et le
--   navigateur ; la base ne gardait que created_by_email. Simon : « ce
--   n'est pas via IP qu'on doit surveiller mais depuis user
--   authentification ». Ce journal note, pour chaque écriture, QUI (jeton
--   authentifié), QUOI et QUAND.
--
-- PAS DE CLÉ ÉTRANGÈRE vers gabarits : l'historique d'un gabarit doit
--   survivre à sa suppression (c'est justement là qu'on le cherche).
--
-- IDEMPOTENCE : CREATE ... IF NOT EXISTS.
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ad_budget.gabarit_journal (
  id              BIGSERIAL PRIMARY KEY,
  gabarit_id      INTEGER,
  organization_id UUID,
  user_id         INTEGER,
  user_email      VARCHAR(255),
  action          VARCHAR(40) NOT NULL,
  detail          JSONB,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS gabarit_journal_gabarit_idx
  ON ad_budget.gabarit_journal (gabarit_id, created_at DESC);

CREATE INDEX IF NOT EXISTS gabarit_journal_org_idx
  ON ad_budget.gabarit_journal (organization_id, created_at DESC);
