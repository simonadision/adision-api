-- ════════════════════════════════════════════════════════════════════
-- Adision Ad BUD — Sprint 6.4 : corrections DONNÉES csi_division_default_metier
-- ════════════════════════════════════════════════════════════════════
-- Demandé par Simon. Migration de DONNÉES uniquement (table déjà créée en
-- sprint 2 ; schéma inchangé). La mécanique d'auto-fill TAUX $ d'Ad EST reste
-- inchangée : niveau Compagnon (_C) + colonne Col 17 (taux_col17), garde
-- anti-écrasement mo_taux_source:auto_division. Le taux auto-rempli reste
-- ÉDITABLE côté front (rien n'est verrouillé ici).
--
-- Corrections appliquées :
--   09 (Finitions)            → POSEUR_SYSINT_C  (corrige : était CHARPENTIER_C)
--   13 (Construction spéciale)→ CHARPENTIER_C    (déjà cette valeur ; no-op confirmé)
--   27 (Communications)       → INSTALL_SECU_C   (corrige : était ELECTRICIEN_C)
--
-- NON appliqué — en attente confirmation Simon :
--   30 & 31 « Opérateur de pelle » : AUCUN métier "Opérateur de pelle" dans la
--     grille ad_budget.taux_horaires. Candidats proches : OP_PELLES_AA_C /
--     OP_PELLES_A_C / OP_PELLES_B_C (Opérateur de pelles mécaniques AA/A/B),
--     ou OP_LOURD_*_C (Opérateur d'équipement lourd). On NE seed PAS d'orphelin
--     (un mapping vers un métier inexistant = TAUX $ vide). 31 conserve donc sa
--     valeur courante (CHARPENTIER_C) ; 30 reste non mappé.
--
-- Résolution STRICTE : chaque code est vérifié contre taux_horaires (actif).
-- Si un code est introuvable → skip + WARNING (jamais d'INSERT orphelin).
-- Idempotent : ON CONFLICT (csi_division) DO UPDATE → ré-exécution no-op.
-- ════════════════════════════════════════════════════════════════════

DO $$
DECLARE
  v_mapping CONSTANT TEXT[][] := ARRAY[
    ARRAY['09', 'POSEUR_SYSINT_C'],
    ARRAY['13', 'CHARPENTIER_C'],
    ARRAY['27', 'INSTALL_SECU_C']
  ];
  v_row      TEXT[];
  v_csi      TEXT;
  v_code     TEXT;
  v_taux_id  INTEGER;
  v_missing  TEXT[] := ARRAY[]::TEXT[];
  v_upserted INTEGER := 0;
BEGIN
  FOREACH v_row SLICE 1 IN ARRAY v_mapping
  LOOP
    v_csi  := v_row[1];
    v_code := v_row[2];

    SELECT id INTO v_taux_id
      FROM ad_budget.taux_horaires
     WHERE code = v_code AND actif = TRUE
     LIMIT 1;

    IF v_taux_id IS NULL THEN
      v_missing := v_missing || v_code;   -- jamais d'orphelin : on skip
      CONTINUE;
    END IF;

    INSERT INTO ad_budget.csi_division_default_metier
      (csi_division, taux_id, updated_by)
    VALUES (v_csi, v_taux_id, 'sprint6_4-2026-06-02')
    ON CONFLICT (csi_division) DO UPDATE
      SET taux_id    = EXCLUDED.taux_id,
          updated_by = EXCLUDED.updated_by;
    v_upserted := v_upserted + 1;
  END LOOP;

  RAISE NOTICE 'Sprint 6.4 — UPSERT divisions 09/13/27 : % réussis', v_upserted;
  IF array_length(v_missing, 1) > 0 THEN
    RAISE WARNING 'Sprint 6.4 — codes métier introuvables (skippés, non seedés) : %',
      array_to_string(v_missing, ', ');
  END IF;
END $$;
