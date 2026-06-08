-- Sprint 3.17 — Métier par défaut de la division CSI 01 = Charpentier-menuisier
-- Compagnon. Règle métier générale (pas propre à un projet), dans la source de
-- vérité partagée ad_budget.csi_division_default_metier (consommée par Ad EST et
-- Ad BUD via /budget/csi-division-defaults). AUCUN code en dur côté apps.
--
-- taux_id résolu par CODE métier (CHARPENTIER_C) et non par id en dur → sûr quel
-- que soit l'environnement. INSERT ... SELECT : si le métier n'existe pas (env
-- sans seed taux), 0 ligne insérée, aucune erreur. Idempotente via ON CONFLICT.

INSERT INTO ad_budget.csi_division_default_metier (csi_division, taux_id, updated_at, updated_by)
SELECT '01', t.id, NOW(), 'migration_3.17'
FROM ad_budget.taux_horaires t
WHERE t.code = 'CHARPENTIER_C'
ON CONFLICT (csi_division)
DO UPDATE SET taux_id = EXCLUDED.taux_id, updated_at = NOW(), updated_by = 'migration_3.17';

DO $$ BEGIN
  RAISE NOTICE 'Migration 3.17 : division CSI 01 -> Charpentier-menuisier Compagnon (CHARPENTIER_C).';
END $$;
