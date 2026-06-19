-- ════════════════════════════════════════════════════════════════════
-- Phase 6 Deploy 3 — ad_budget.projets.nom devient NULLABLE
-- ════════════════════════════════════════════════════════════════════
-- L'identité projet (dont le nom) est lue depuis Ad HUB (source unique) ;
-- plus aucune écriture LOCALE d'identité (create/duplicate/reviser nettoyés
-- en Deploy 3). `nom` était la SEULE colonne d'identité NOT NULL SANS default
-- → on la rend nullable pour que les INSERT puissent l'omettre comme les autres.
--
-- Idempotent : DROP NOT NULL sur une colonne déjà nullable = no-op (rejoué sans
-- effet). Réversible : ALTER COLUMN nom SET NOT NULL (mais ne PAS réintroduire ;
-- la colonne est DROPpée en Phase 7).
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE ad_budget.projets ALTER COLUMN nom DROP NOT NULL;
