-- Brief Simon, 8 sept 2026, en direct, capture à l'appui (item ajouté dans
-- "02 — Divers" hérite de 71,02 $/h, "Manœuvre spécialisé") : "quand un
-- nouvelle item est ajouter et aucun taux horaire nest défini, appliquer le
-- taux charpentier menuisier compagnon par defaut."
--
-- Même mécanisme que Sprint 3.17 (division 01) : le métier par défaut d'une
-- division CSI vit dans ad_budget.csi_division_default_metier (source de
-- vérité partagée Ad EST/Ad BUD, consommée par _resolve_taux_default /
-- _load_taux_default_map, modules/taux_horaires_api.py) -- AUCUN code en dur
-- côté apps. Les divisions 02/32/33 pointaient vers OCC_MANOEUVRE_SPEC
-- (Manœuvre spécialisé, sprint6_5/6_6, 2 juin 2026) -- repointées ici vers
-- CHARPENTIER_C (Charpentier-menuisier, Compagnon), comme la division 01
-- depuis Sprint 3.17.
--
-- taux_id résolu par CODE métier (CHARPENTIER_C) et non par id en dur → sûr
-- quel que soit l'environnement. UPDATE ciblé sur les 3 divisions concernées
-- uniquement -- les autres divisions gardent leur métier spécifique
-- (électricien pour 26, plombier pour 22, etc.), ce n'est pas un défaut
-- universel qui écraserait tout le système de métiers par division.

UPDATE ad_budget.csi_division_default_metier m
SET taux_id = t.id, updated_at = NOW(), updated_by = 'migration_charpentier_02_32_33'
FROM ad_budget.taux_horaires t
WHERE t.code = 'CHARPENTIER_C'
  AND m.csi_division IN ('02', '32', '33');

DO $$ BEGIN
  RAISE NOTICE 'Migration : divisions CSI 02/32/33 -> Charpentier-menuisier Compagnon (CHARPENTIER_C), au lieu de Manœuvre spécialisé.';
END $$;
