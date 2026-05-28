-- ════════════════════════════════════════════════════════════════════
-- Adision Ad BUD — Sprint 6 PIVOT : enrich taux_horaires 4 niveaux ACQ
-- ════════════════════════════════════════════════════════════════════
--
-- Décision archi Sprint 6 PIVOT (28 mai 2026) : centraliser la grille
-- ACQ dans adision-api/ad_budget.taux_horaires comme single source of
-- truth pour Ad BUD ET Ad EST. La table existante n'expose qu'UN seul
-- niveau de taux (taux_col17 = col 17 PDF). Pour le picker 4 niveaux
-- d'Ad EST, on enrichit avec les 3 niveaux supplémentaires :
--
--   taux_salaire      : col 1  PDF — salaire net employé
--   salaire_brut      : col 3  PDF — (1) + vacances 13 %
--   taux_col17        : col 17 PDF — existant, inchangé (rétrocompat Ad BUD)
--   total_avant_admin : col 20 PDF — (17) + camions + outils
--
-- Source CSV : migrations/acq_rates_by_label.csv (175 lignes, fournies
-- par Simon depuis le PDF ACQ 2026-04-26).
--
-- Matching réalisé par script Python (4 passes) : exact (metier+qual
-- normalisés) → qual+chmo → metier substring+chmo → groupe+chmo fallback.
-- Cross-validation : CSV.cout_horaire_mo == BD.taux_col17 ✓ 0 mismatch.
-- Non-syndiqués : taux_col17 BD stocke col 20 (cf. mig 014), cohérent
-- avec CSV.total_avant_admin ✓ 0 mismatch. Pour ces lignes, on laisse
-- cout_horaire_mo NULL (pas de col 17 PDF pour les non-syndiqués).
--
-- ⚠️ Compat : Ad BUD continue à consommer taux_col17 sans changement.
-- Les 3 nouvelles colonnes sont NULL avant cette migration, remplies
-- pour les 175 lignes existantes après. Le endpoint /budget/taux-horaires
-- sera enrichi (sortie SELECT) dans le même PR.
--
-- Idempotent : ADD COLUMN IF NOT EXISTS + UPDATE ne crée pas de
-- doublons. UPDATE peut être ré-exécuté sans dommage (les valeurs
-- sont écrasées par les mêmes valeurs).
-- ════════════════════════════════════════════════════════════════════

-- Step 1 — Ajout des 3 colonnes (idempotent).
ALTER TABLE ad_budget.taux_horaires
  ADD COLUMN IF NOT EXISTS taux_salaire      NUMERIC(8, 2),
  ADD COLUMN IF NOT EXISTS salaire_brut      NUMERIC(8, 2),
  ADD COLUMN IF NOT EXISTS total_avant_admin NUMERIC(8, 2);

COMMENT ON COLUMN ad_budget.taux_horaires.taux_salaire IS
  'ACQ col 1 — salaire net employé (avant cotisations). Sprint 6 PIVOT.';
COMMENT ON COLUMN ad_budget.taux_horaires.salaire_brut IS
  'ACQ col 3 — taux_salaire + vacances 13 %. Sprint 6 PIVOT.';
COMMENT ON COLUMN ad_budget.taux_horaires.total_avant_admin IS
  'ACQ col 20 — taux_col17 + camions + outils. Recommandé pour soumission. Sprint 6 PIVOT.';

-- Step 2 — Remplissage des 175 lignes (1 UPDATE par code).

-- ─── METIER (137 lignes) ───────────────────────────────
UPDATE ad_budget.taux_horaires SET taux_salaire = 49.57, salaire_brut = 56.01, total_avant_admin = 100.83 WHERE code = 'BRIQUETEUR_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 29.74, salaire_brut = 33.61, total_avant_admin = 70.10 WHERE code = 'BRIQUETEUR_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 34.70, salaire_brut = 39.21, total_avant_admin = 77.55 WHERE code = 'BRIQUETEUR_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 42.13, salaire_brut = 47.61, total_avant_admin = 88.74 WHERE code = 'BRIQUETEUR_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.79, salaire_brut = 57.39, total_avant_admin = 99.99 WHERE code = 'CALORIFUGEUR_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 30.47, salaire_brut = 34.43, total_avant_admin = 69.57 WHERE code = 'CALORIFUGEUR_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.55, salaire_brut = 40.17, total_avant_admin = 76.93 WHERE code = 'CALORIFUGEUR_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.17, salaire_brut = 48.78, total_avant_admin = 88.01 WHERE code = 'CALORIFUGEUR_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.12, salaire_brut = 56.64, total_avant_admin = 100.81 WHERE code = 'CARRELEUR_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 30.07, salaire_brut = 33.98, total_avant_admin = 70.06 WHERE code = 'CARRELEUR_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.08, salaire_brut = 39.64, total_avant_admin = 77.53 WHERE code = 'CARRELEUR_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 42.60, salaire_brut = 48.14, total_avant_admin = 88.70 WHERE code = 'CARRELEUR_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.16, salaire_brut = 56.68, total_avant_admin = 100.87 WHERE code = 'CHARPENTIER_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 30.10, salaire_brut = 34.01, total_avant_admin = 70.10 WHERE code = 'CHARPENTIER_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.11, salaire_brut = 39.67, total_avant_admin = 77.56 WHERE code = 'CHARPENTIER_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 42.64, salaire_brut = 48.18, total_avant_admin = 88.77 WHERE code = 'CHARPENTIER_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.79, salaire_brut = 57.39, total_avant_admin = 100.20 WHERE code = 'CHAUDRONNIER_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 30.47, salaire_brut = 34.43, total_avant_admin = 69.77 WHERE code = 'CHAUDRONNIER_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.55, salaire_brut = 40.17, total_avant_admin = 77.14 WHERE code = 'CHAUDRONNIER_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.17, salaire_brut = 48.78, total_avant_admin = 88.20 WHERE code = 'CHAUDRONNIER_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 48.55, salaire_brut = 54.86, total_avant_admin = 99.14 WHERE code = 'CIMENTIER_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 33.99, salaire_brut = 38.41, total_avant_admin = 76.39 WHERE code = 'CIMENTIER_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 41.27, salaire_brut = 46.64, total_avant_admin = 87.31 WHERE code = 'CIMENTIER_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 51.20, salaire_brut = 57.86, total_avant_admin = 104.05 WHERE code = 'COUVREUR_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.84, salaire_brut = 40.50, total_avant_admin = 79.96 WHERE code = 'COUVREUR_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.52, salaire_brut = 49.18, total_avant_admin = 91.57 WHERE code = 'COUVREUR_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.79, salaire_brut = 57.39, total_avant_admin = 102.25 WHERE code = 'ELECTRICIEN_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 25.40, salaire_brut = 28.70, total_avant_admin = 63.69 WHERE code = 'ELECTRICIEN_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 30.47, salaire_brut = 34.43, total_avant_admin = 71.21 WHERE code = 'ELECTRICIEN_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.55, salaire_brut = 40.17, total_avant_admin = 78.73 WHERE code = 'ELECTRICIEN_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.17, salaire_brut = 48.78, total_avant_admin = 90.02 WHERE code = 'ELECTRICIEN_A4';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.79, salaire_brut = 57.39, total_avant_admin = 101.35 WHERE code = 'FERBLANTIER_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 30.47, salaire_brut = 34.43, total_avant_admin = 70.37 WHERE code = 'FERBLANTIER_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.55, salaire_brut = 40.17, total_avant_admin = 77.89 WHERE code = 'FERBLANTIER_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.17, salaire_brut = 48.78, total_avant_admin = 89.16 WHERE code = 'FERBLANTIER_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 51.31, salaire_brut = 57.98, total_avant_admin = 103.52 WHERE code = 'ARMATURE_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.61, salaire_brut = 49.28, total_avant_admin = 91.06 WHERE code = 'ARMATURE_A';
UPDATE ad_budget.taux_horaires SET taux_salaire = 53.33, salaire_brut = 60.26, total_avant_admin = 103.89 WHERE code = 'FRIGORISTE_HALO';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.79, salaire_brut = 57.39, total_avant_admin = 100.20 WHERE code = 'FRIGORISTE_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 25.40, salaire_brut = 28.70, total_avant_admin = 62.46 WHERE code = 'FRIGORISTE_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 30.47, salaire_brut = 34.43, total_avant_admin = 69.83 WHERE code = 'FRIGORISTE_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.55, salaire_brut = 40.17, total_avant_admin = 77.18 WHERE code = 'FRIGORISTE_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.17, salaire_brut = 48.78, total_avant_admin = 88.24 WHERE code = 'FRIGORISTE_A4';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.79, salaire_brut = 57.39, total_avant_admin = 100.00 WHERE code = 'GRUTIER_A_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.55, salaire_brut = 40.17, total_avant_admin = 76.81 WHERE code = 'GRUTIER_A_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.17, salaire_brut = 48.78, total_avant_admin = 87.95 WHERE code = 'GRUTIER_A_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 48.58, salaire_brut = 54.90, total_avant_admin = 96.78 WHERE code = 'GRUTIER_B_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 34.01, salaire_brut = 38.43, total_avant_admin = 74.57 WHERE code = 'GRUTIER_B_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 41.29, salaire_brut = 46.66, total_avant_admin = 85.21 WHERE code = 'GRUTIER_B_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 56.70, salaire_brut = 64.07, total_avant_admin = 108.16 WHERE code = 'MECA_ASCENSEUR_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 28.35, salaire_brut = 32.04, total_avant_admin = 66.04 WHERE code = 'MECA_ASCENSEUR_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 34.02, salaire_brut = 38.44, total_avant_admin = 74.28 WHERE code = 'MECA_ASCENSEUR_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 39.69, salaire_brut = 44.85, total_avant_admin = 82.53 WHERE code = 'MECA_ASCENSEUR_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 48.20, salaire_brut = 54.47, total_avant_admin = 94.88 WHERE code = 'MECA_ASCENSEUR_A4';
UPDATE ad_budget.taux_horaires SET taux_salaire = 48.20, salaire_brut = 54.47, total_avant_admin = 94.88 WHERE code = 'MECA_ASCENSEUR_A5';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.79, salaire_brut = 57.39, total_avant_admin = 100.26 WHERE code = 'MECA_CHANTIER_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 30.47, salaire_brut = 34.43, total_avant_admin = 69.83 WHERE code = 'MECA_CHANTIER_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.55, salaire_brut = 40.17, total_avant_admin = 77.21 WHERE code = 'MECA_CHANTIER_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.17, salaire_brut = 48.78, total_avant_admin = 88.28 WHERE code = 'MECA_CHANTIER_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 48.94, salaire_brut = 55.30, total_avant_admin = 98.16 WHERE code = 'MECA_LOURDES_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 29.36, salaire_brut = 33.18, total_avant_admin = 68.63 WHERE code = 'MECA_LOURDES_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 34.26, salaire_brut = 38.71, total_avant_admin = 75.79 WHERE code = 'MECA_LOURDES_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 41.60, salaire_brut = 47.01, total_avant_admin = 86.52 WHERE code = 'MECA_LOURDES_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.79, salaire_brut = 57.39, total_avant_admin = 100.38 WHERE code = 'MECA_PROFEU_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 25.40, salaire_brut = 28.70, total_avant_admin = 62.56 WHERE code = 'MECA_PROFEU_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 30.47, salaire_brut = 34.43, total_avant_admin = 69.94 WHERE code = 'MECA_PROFEU_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.55, salaire_brut = 40.17, total_avant_admin = 77.31 WHERE code = 'MECA_PROFEU_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.17, salaire_brut = 48.78, total_avant_admin = 88.39 WHERE code = 'MECA_PROFEU_A4';
UPDATE ad_budget.taux_horaires SET taux_salaire = 51.31, salaire_brut = 57.98, total_avant_admin = 103.64 WHERE code = 'MONTEUR_ASS_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 30.79, salaire_brut = 34.79, total_avant_admin = 71.92 WHERE code = 'MONTEUR_ASS_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.92, salaire_brut = 40.59, total_avant_admin = 79.62 WHERE code = 'MONTEUR_ASS_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.61, salaire_brut = 49.28, total_avant_admin = 91.18 WHERE code = 'MONTEUR_ASS_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 49.60, salaire_brut = 56.05, total_avant_admin = 100.50 WHERE code = 'VITRIER_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 29.76, salaire_brut = 33.63, total_avant_admin = 69.83 WHERE code = 'VITRIER_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 34.72, salaire_brut = 39.23, total_avant_admin = 77.27 WHERE code = 'VITRIER_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 42.16, salaire_brut = 47.64, total_avant_admin = 88.43 WHERE code = 'VITRIER_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.16, salaire_brut = 56.68, total_avant_admin = 100.87 WHERE code = 'PARQUETEUR_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 30.10, salaire_brut = 34.01, total_avant_admin = 70.10 WHERE code = 'PARQUETEUR_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.11, salaire_brut = 39.67, total_avant_admin = 77.56 WHERE code = 'PARQUETEUR_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 42.64, salaire_brut = 48.18, total_avant_admin = 88.77 WHERE code = 'PARQUETEUR_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 47.39, salaire_brut = 53.55, total_avant_admin = 96.49 WHERE code = 'PEINTRE_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 28.43, salaire_brut = 32.13, total_avant_admin = 67.37 WHERE code = 'PEINTRE_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 33.17, salaire_brut = 37.48, total_avant_admin = 74.41 WHERE code = 'PEINTRE_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 40.28, salaire_brut = 45.52, total_avant_admin = 85.00 WHERE code = 'PEINTRE_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 48.26, salaire_brut = 54.53, total_avant_admin = 98.03 WHERE code = 'PLATRIER_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 28.96, salaire_brut = 32.72, total_avant_admin = 68.43 WHERE code = 'PLATRIER_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 33.78, salaire_brut = 38.17, total_avant_admin = 75.59 WHERE code = 'PLATRIER_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 41.02, salaire_brut = 46.35, total_avant_admin = 86.36 WHERE code = 'PLATRIER_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 49.07, salaire_brut = 55.45, total_avant_admin = 99.14 WHERE code = 'POSEUR_SOUPLES_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 29.44, salaire_brut = 33.27, total_avant_admin = 69.03 WHERE code = 'POSEUR_SOUPLES_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 34.35, salaire_brut = 38.82, total_avant_admin = 76.33 WHERE code = 'POSEUR_SOUPLES_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 41.71, salaire_brut = 47.13, total_avant_admin = 87.27 WHERE code = 'POSEUR_SOUPLES_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.16, salaire_brut = 56.68, total_avant_admin = 101.37 WHERE code = 'POSEUR_SYSINT_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 30.10, salaire_brut = 34.01, total_avant_admin = 70.60 WHERE code = 'POSEUR_SYSINT_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.11, salaire_brut = 39.67, total_avant_admin = 78.06 WHERE code = 'POSEUR_SYSINT_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 42.64, salaire_brut = 48.18, total_avant_admin = 89.26 WHERE code = 'POSEUR_SYSINT_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 47.39, salaire_brut = 53.55, total_avant_admin = 96.73 WHERE code = 'TIREUR_PL_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 28.43, salaire_brut = 32.13, total_avant_admin = 67.62 WHERE code = 'TIREUR_PL_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 33.17, salaire_brut = 37.48, total_avant_admin = 74.69 WHERE code = 'TIREUR_PL_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 40.28, salaire_brut = 45.52, total_avant_admin = 85.26 WHERE code = 'TIREUR_PL_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 47.51, salaire_brut = 53.69, total_avant_admin = 96.68 WHERE code = 'TIREUR_PE_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 28.51, salaire_brut = 32.22, total_avant_admin = 67.49 WHERE code = 'TIREUR_PE_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 33.26, salaire_brut = 37.58, total_avant_admin = 74.55 WHERE code = 'TIREUR_PE_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 40.38, salaire_brut = 45.63, total_avant_admin = 85.15 WHERE code = 'TIREUR_PE_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.79, salaire_brut = 57.39, total_avant_admin = 100.63 WHERE code = 'TUYAUTEUR_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 25.40, salaire_brut = 28.70, total_avant_admin = 62.83 WHERE code = 'TUYAUTEUR_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 30.47, salaire_brut = 34.43, total_avant_admin = 70.21 WHERE code = 'TUYAUTEUR_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.55, salaire_brut = 40.17, total_avant_admin = 77.57 WHERE code = 'TUYAUTEUR_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.17, salaire_brut = 48.78, total_avant_admin = 88.64 WHERE code = 'TUYAUTEUR_A4';
UPDATE ad_budget.taux_horaires SET taux_salaire = 41.66, salaire_brut = 47.08, total_avant_admin = 86.68 WHERE code = 'INSTALL_SECU_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 24.99, salaire_brut = 28.24, total_avant_admin = 61.74 WHERE code = 'INSTALL_SECU_A1';
UPDATE ad_budget.taux_horaires SET taux_salaire = 29.16, salaire_brut = 32.95, total_avant_admin = 67.75 WHERE code = 'INSTALL_SECU_A2';
UPDATE ad_budget.taux_horaires SET taux_salaire = 35.41, salaire_brut = 40.01, total_avant_admin = 76.76 WHERE code = 'INSTALL_SECU_A3';
UPDATE ad_budget.taux_horaires SET taux_salaire = 48.23, salaire_brut = 54.50, total_avant_admin = 97.12 WHERE code = 'OP_LOURD_AA_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 41.00, salaire_brut = 46.33, total_avant_admin = 85.65 WHERE code = 'OP_LOURD_AA_A';
UPDATE ad_budget.taux_horaires SET taux_salaire = 46.69, salaire_brut = 52.76, total_avant_admin = 94.89 WHERE code = 'OP_LOURD_A_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 39.69, salaire_brut = 44.85, total_avant_admin = 83.74 WHERE code = 'OP_LOURD_A_A';
UPDATE ad_budget.taux_horaires SET taux_salaire = 45.49, salaire_brut = 51.40, total_avant_admin = 93.13 WHERE code = 'OP_LOURD_B_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 38.67, salaire_brut = 43.70, total_avant_admin = 82.25 WHERE code = 'OP_LOURD_B_A';
UPDATE ad_budget.taux_horaires SET taux_salaire = 51.51, salaire_brut = 58.21, total_avant_admin = 101.93 WHERE code = 'OP_PELLES_AA_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.78, salaire_brut = 49.47, total_avant_admin = 89.71 WHERE code = 'OP_PELLES_AA_A';
UPDATE ad_budget.taux_horaires SET taux_salaire = 49.92, salaire_brut = 56.41, total_avant_admin = 99.60 WHERE code = 'OP_PELLES_A_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 42.43, salaire_brut = 47.95, total_avant_admin = 87.74 WHERE code = 'OP_PELLES_A_A';
UPDATE ad_budget.taux_horaires SET taux_salaire = 48.32, salaire_brut = 54.60, total_avant_admin = 97.26 WHERE code = 'OP_PELLES_B_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 41.07, salaire_brut = 46.41, total_avant_admin = 85.75 WHERE code = 'OP_PELLES_B_A';
UPDATE ad_budget.taux_horaires SET taux_salaire = 45.89, salaire_brut = 51.86, total_avant_admin = 94.74 WHERE code = 'OP_PBETON_42M_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 39.01, salaire_brut = 44.08, total_avant_admin = 83.50 WHERE code = 'OP_PBETON_42M_A';
UPDATE ad_budget.taux_horaires SET taux_salaire = 47.89, salaire_brut = 54.12, total_avant_admin = 97.74 WHERE code = 'OP_PBETON_42P_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 40.71, salaire_brut = 46.00, total_avant_admin = 86.05 WHERE code = 'OP_PBETON_42P_A';
UPDATE ad_budget.taux_horaires SET taux_salaire = 48.89, salaire_brut = 55.25, total_avant_admin = 99.23 WHERE code = 'OP_PBETON_50P_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 41.56, salaire_brut = 46.96, total_avant_admin = 87.30 WHERE code = 'OP_PBETON_50P_A';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.89, salaire_brut = 57.51, total_avant_admin = 102.24 WHERE code = 'OP_PBETON_58P_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.26, salaire_brut = 48.88, total_avant_admin = 89.86 WHERE code = 'OP_PBETON_58P_A';
UPDATE ad_budget.taux_horaires SET taux_salaire = 54.28, salaire_brut = 61.34, total_avant_admin = 107.33 WHERE code = 'OP_PBETON_63P_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 46.14, salaire_brut = 52.14, total_avant_admin = 94.20 WHERE code = 'OP_PBETON_63P_A';
UPDATE ad_budget.taux_horaires SET taux_salaire = 54.17, salaire_brut = 61.21, total_avant_admin = 106.83 WHERE code = 'CHEF_EQUIPE_CHARP';
UPDATE ad_budget.taux_horaires SET taux_salaire = 55.18, salaire_brut = 62.35, total_avant_admin = 108.32 WHERE code = 'CHEF_GROUPE_CHARP';

-- ─── OCCUPATION (29 lignes) ───────────────────────────────
UPDATE ad_budget.taux_horaires SET taux_salaire = 40.78, salaire_brut = 46.08, total_avant_admin = 85.68 WHERE code = 'OCC_CHAUFFEUR_VAPEUR';
UPDATE ad_budget.taux_horaires SET taux_salaire = 36.21, salaire_brut = 40.92, total_avant_admin = 79.04 WHERE code = 'OCC_CHAUFFEUR_4';
UPDATE ad_budget.taux_horaires SET taux_salaire = 27.78, salaire_brut = 31.39, total_avant_admin = 67.85 WHERE code = 'OCC_COMMIS';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.23, salaire_brut = 48.85, total_avant_admin = 89.83 WHERE code = 'OCC_COND_CAMION_AA';
UPDATE ad_budget.taux_horaires SET taux_salaire = 41.67, salaire_brut = 47.09, total_avant_admin = 87.56 WHERE code = 'OCC_COND_CAMION_A';
UPDATE ad_budget.taux_horaires SET taux_salaire = 40.94, salaire_brut = 46.26, total_avant_admin = 86.49 WHERE code = 'OCC_COND_CAMION_B';
UPDATE ad_budget.taux_horaires SET taux_salaire = 40.41, salaire_brut = 45.66, total_avant_admin = 85.70 WHERE code = 'OCC_COND_CAMION_C';
UPDATE ad_budget.taux_horaires SET taux_salaire = 20.75, salaire_brut = 23.45, total_avant_admin = 57.39 WHERE code = 'OCC_GARDIEN';
UPDATE ad_budget.taux_horaires SET taux_salaire = 39.44, salaire_brut = 44.57, total_avant_admin = 84.08 WHERE code = 'OCC_HOMME_SERVICE_ML';
UPDATE ad_budget.taux_horaires SET taux_salaire = 33.09, salaire_brut = 37.39, total_avant_admin = 75.74 WHERE code = 'OCC_MAGASINIER';
UPDATE ad_budget.taux_horaires SET taux_salaire = 40.19, salaire_brut = 45.41, total_avant_admin = 86.28 WHERE code = 'OCC_MANOEUVRE_JOURN';
UPDATE ad_budget.taux_horaires SET taux_salaire = 40.83, salaire_brut = 46.14, total_avant_admin = 86.12 WHERE code = 'OCC_MANOEUVRE_PIPE';
UPDATE ad_budget.taux_horaires SET taux_salaire = 40.45, salaire_brut = 45.71, total_avant_admin = 87.58 WHERE code = 'OCC_MANOEUVRE_COUVERT';
UPDATE ad_budget.taux_horaires SET taux_salaire = 41.91, salaire_brut = 47.36, total_avant_admin = 89.57 WHERE code = 'OCC_MANOEUVRE_MACO';
UPDATE ad_budget.taux_horaires SET taux_salaire = 45.22, salaire_brut = 51.10, total_avant_admin = 93.78 WHERE code = 'OCC_MANOEUVRE_DECON';
UPDATE ad_budget.taux_horaires SET taux_salaire = 40.83, salaire_brut = 46.14, total_avant_admin = 87.25 WHERE code = 'OCC_MANOEUVRE_SPEC';
UPDATE ad_budget.taux_horaires SET taux_salaire = 42.04, salaire_brut = 47.51, total_avant_admin = 88.61 WHERE code = 'OCC_MANOEUVRE_CARR';
UPDATE ad_budget.taux_horaires SET taux_salaire = 41.09, salaire_brut = 46.43, total_avant_admin = 88.55 WHERE code = 'OCC_MANOEUVRE_COUV';
UPDATE ad_budget.taux_horaires SET taux_salaire = 41.70, salaire_brut = 47.12, total_avant_admin = 87.59 WHERE code = 'OCC_GENERATRICE';
UPDATE ad_budget.taux_horaires SET taux_salaire = 47.40, salaire_brut = 53.56, total_avant_admin = 95.28 WHERE code = 'OCC_SOUDEUR';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.79, salaire_brut = 57.39, total_avant_admin = 100.63 WHERE code = 'OCC_SOUDEUR_TUYAU';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.33, salaire_brut = 56.87, total_avant_admin = 99.54 WHERE code = 'OCC_GASFITTER';
UPDATE ad_budget.taux_horaires SET taux_salaire = 44.38, salaire_brut = 50.15, total_avant_admin = 91.52 WHERE code = 'OCC_LEVAGE_A';
UPDATE ad_budget.taux_horaires SET taux_salaire = 42.54, salaire_brut = 48.07, total_avant_admin = 88.82 WHERE code = 'OCC_LEVAGE_B';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.28, salaire_brut = 48.91, total_avant_admin = 90.38 WHERE code = 'OCC_USINES';
UPDATE ad_budget.taux_horaires SET taux_salaire = 44.47, salaire_brut = 50.25, total_avant_admin = 91.42 WHERE code = 'OCC_PNEUS_DEBOSS';
UPDATE ad_budget.taux_horaires SET taux_salaire = 50.79, salaire_brut = 57.39, total_avant_admin = 100.48 WHERE code = 'OCC_SOUDEUR_PIPELINE';
UPDATE ad_budget.taux_horaires SET taux_salaire = 42.86, salaire_brut = 48.43, total_avant_admin = 89.06 WHERE code = 'OCC_POMPES_COMP';
UPDATE ad_budget.taux_horaires SET taux_salaire = 42.86, salaire_brut = 48.43, total_avant_admin = 89.06 WHERE code = 'OCC_POMPES_LIGNE';

-- ─── NON_SYNDIQUE (9 lignes) ───────────────────────────────
UPDATE ad_budget.taux_horaires SET taux_salaire = 53.64, salaire_brut = 53.64, total_avant_admin = 107.28 WHERE code = 'NS_AGENT_SST';
UPDATE ad_budget.taux_horaires SET taux_salaire = 65.02, salaire_brut = 65.02, total_avant_admin = 149.55 WHERE code = 'NS_ESTIMATEUR';
UPDATE ad_budget.taux_horaires SET taux_salaire = 69.66, salaire_brut = 69.66, total_avant_admin = 139.32 WHERE code = 'NS_GERANT_PROJET';
UPDATE ad_budget.taux_horaires SET taux_salaire = 65.30, salaire_brut = 65.30, total_avant_admin = 150.19 WHERE code = 'NS_INGENIEUR';
UPDATE ad_budget.taux_horaires SET taux_salaire = 24.39, salaire_brut = 24.39, total_avant_admin = 56.10 WHERE code = 'NS_PERS_AUX';
UPDATE ad_budget.taux_horaires SET taux_salaire = 70.34, salaire_brut = 70.34, total_avant_admin = 140.68 WHERE code = 'NS_SURINTENDANT';
UPDATE ad_budget.taux_horaires SET taux_salaire = 39.89, salaire_brut = 39.89, total_avant_admin = 91.75 WHERE code = 'NS_TECH_IMMO_JR';
UPDATE ad_budget.taux_horaires SET taux_salaire = 47.41, salaire_brut = 47.41, total_avant_admin = 109.04 WHERE code = 'NS_TECH_IMMO';
UPDATE ad_budget.taux_horaires SET taux_salaire = 43.67, salaire_brut = 43.67, total_avant_admin = 100.44 WHERE code = 'NS_TECH_ADMIN';

-- Step 3 — Garde-fou : vérifier que les 175 lignes sont remplies.
DO $$
DECLARE
  v_filled INT;
  v_total INT;
  v_unfilled INT;
BEGIN
  SELECT COUNT(*) INTO v_total FROM ad_budget.taux_horaires;
  SELECT COUNT(*) INTO v_filled FROM ad_budget.taux_horaires
   WHERE taux_salaire IS NOT NULL
     AND salaire_brut IS NOT NULL
     AND total_avant_admin IS NOT NULL;
  v_unfilled := v_total - v_filled;
  RAISE NOTICE 'Enrichissement ACQ 2026 : % / % lignes remplies (taux_salaire + salaire_brut + total_avant_admin)', v_filled, v_total;
  IF v_unfilled > 0 THEN
    RAISE WARNING 'ATTENTION : % lignes sans taux_salaire (codes non matchés ?)', v_unfilled;
  END IF;
END $$;
