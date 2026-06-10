-- Garde STRUCTUREL anti-double-budget (incident "Presse de compaction").
-- Un projet Ad HUB ne peut avoir qu'UN budget ACTIF (statut <> 'archive').
-- Plusieurs budgets ARCHIVÉS pour le même HUB = autorisé (historique).
-- Les budgets sans lien HUB (ad_hub_project_id NULL) ne sont PAS contraints.
-- Idempotent (IF NOT EXISTS). Build sûr : 0 doublon actif confirmé avant migration.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_budget_actif_par_hub
  ON ad_budget.projets (ad_hub_project_id)
  WHERE statut <> 'archive' AND ad_hub_project_id IS NOT NULL;
