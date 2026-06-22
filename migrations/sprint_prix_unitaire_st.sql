-- ════════════════════════════════════════════════════════════════════════════
-- Sprint PRIX UNITAIRE SOUS-TRAITANT (PU_ST)
--
-- Mode COMPUTED : total ST = qte × prix_unitaire_st (sans ajustement intégré,
-- l'ajust % est appliqué par getRow front + PDF backend comme avant). Mode
-- LUMP SUM préservé : sous_traitant_montant reste éditable en bloc si PU_ST = 0
-- (rétrocompat). Le mode COMPUTED prime quand PU_ST > 0.
--
-- Pas de backfill : lignes existantes restent en mode lump sum tant que
-- PU_ST n'est pas saisi. Idempotente (replay-safe runner Railway).
-- ════════════════════════════════════════════════════════════════════════════

ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS prix_unitaire_st          NUMERIC(14,4) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS prix_unitaire_st_override BOOLEAN       NOT NULL DEFAULT FALSE;
