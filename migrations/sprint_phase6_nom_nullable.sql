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
--
-- 7 août 2026 — garde ajoutée pour l'INSTALLATION NEUVE. `projets` n'était
-- créée par AUCUN fichier du dépôt ; elle l'est maintenant par
-- 000_rattrapage_socle.sql, qui la pose dans sa forme d'AUJOURD'HUI — donc
-- SANS `nom`, supprimée en Phase 7B. Sur une base vide, l'ALTER nu échouait
-- ici et cassait la chaîne. En production la migration est déjà inscrite dans
-- schema_migrations : elle n'est plus jamais rejouée, la garde n'y change rien.
-- ════════════════════════════════════════════════════════════════════

DO $phase6$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'ad_budget' AND table_name = 'projets'
                 AND column_name = 'nom') THEN
        ALTER TABLE ad_budget.projets ALTER COLUMN nom DROP NOT NULL;
    END IF;
END $phase6$;
