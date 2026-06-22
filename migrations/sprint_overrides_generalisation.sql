-- ════════════════════════════════════════════════════════════════════════════
-- Sprint OVERRIDES GÉNÉRALISATION — flags par champ override-able
--
-- Règle métier non négociable : la SAISIE MANUELLE UTILISATEUR domine TOUJOURS
-- la valeur du carnet (Ad TYP, Ad MAT, gabarit, master). Aucun resync silencieux
-- (apply-typ auto, push master, recalcul) ne doit écraser un champ override.
-- Aligné sur le pattern existant prix_unitaire_override + heures_manuelles.
--
-- Idempotente (replay-safe sur Railway, runner ad-budget). DEFAULT FALSE :
-- lignes existantes -> comportement actuel (resync écrit) ; premier PUT après
-- migration pose le flag -> comportement override-aware.
-- ════════════════════════════════════════════════════════════════════════════

ALTER TABLE ad_budget.budget_lignes
  ADD COLUMN IF NOT EXISTS qte_override                    BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS taux_horaire_override           BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS ajust_materiaux_override        BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS ajust_main_oeuvre_override      BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS ajust_sous_traitant_override    BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS sous_traitant_montant_override  BOOLEAN NOT NULL DEFAULT FALSE;
