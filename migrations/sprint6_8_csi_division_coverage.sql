-- ════════════════════════════════════════════════════════════════════
-- Ad BUD — sprint6_8 : FILET DE SÉCURITÉ couverture division→métier CSI
-- ════════════════════════════════════════════════════════════════════
-- Inventaire (3 juin) : TOUTES les divisions CSI présentes sur des items du
-- catalogue Ad MAT (via categories.code_csi) sont déjà mappées dans les
-- migrations sprint2 / 6.2 / 6.3 (+ raffinements 6_4–6_7). Aucune division
-- orpheline trouvée AU NIVEAU SOURCE. Divisions au catalogue :
--   02,03,04,05,06,07,08,09,10,11,12,14,21,22,23,26,27,31.
--
-- Comme le déploiement adision-api a déjà échoué en silence par le passé
-- (attestations mise → migrations non montées), et que l'objectif est qu'AUCUNE
-- ligne classée CSI ne reste à taux 0, cette migration RÉ-ASSERTE le mapping de
-- chaque division présente au catalogue, avec le métier ÉTABLI (état sprint6_x).
--
-- ⚠ PUR AJOUT : ON CONFLICT (csi_division) DO NOTHING → n'ÉCRASE JAMAIS un
-- mapping existant (un métier déjà posé, même raffiné, est conservé). Ne fait
-- donc QUE combler une éventuelle division manquante (gap de déploiement). Taux
-- = Compagnon/occupation (le endpoint /budget/csi-division-defaults expose
-- taux_col17 = Col 17 ; Ad EST consomme via la cascade section→division→manuel,
-- inchangée). Résolution stricte par taux_horaires.code actif → aucune ligne
-- orpheline insérée (skip silencieux si un code manquait). Idempotent (run-once
-- via schema_migrations ; et DO NOTHING de toute façon).
-- ════════════════════════════════════════════════════════════════════

INSERT INTO ad_budget.csi_division_default_metier (csi_division, taux_id, updated_by)
SELECT v.div, t.id, 'sprint6_8_coverage'
FROM (VALUES
    ('02', 'OCC_MANOEUVRE_SPEC'),   -- Démolition / décontamination → manœuvre spécialisé
    ('03', 'CIMENTIER_C'),          -- Béton → cimentier-applicateur
    ('04', 'BRIQUETEUR_C'),         -- Maçonnerie → briqueteur-maçon
    ('05', 'CHARPENTIER_C'),        -- Métaux (fallback ; sections 05 12/05 50 → MONTEUR_ASS_C)
    ('06', 'CHARPENTIER_C'),        -- Bois & plastiques → charpentier-menuisier
    ('07', 'COUVREUR_C'),           -- Thermique/humidité (sections raffinées 07 21/46/50/81/90/92)
    ('08', 'VITRIER_C'),            -- Ouvertures (sections raffinées 08 10/11/14/50/80)
    ('09', 'POSEUR_SYSINT_C'),      -- Finitions/systèmes intérieurs (sections raffinées 09 xx)
    ('10', 'CHARPENTIER_C'),        -- Spécialités → charpentier-menuisier
    ('11', 'MECA_CHANTIER_C'),      -- Équipement → mécanicien de chantier
    ('12', 'CHARPENTIER_C'),        -- Ameublement → charpentier-menuisier
    ('14', 'MECA_ASCENSEUR_C'),     -- Transport (ascenseurs) → mécanicien d'ascenseurs
    ('21', 'TUYAUTEUR_C'),          -- Protection incendie → tuyauteur
    ('22', 'TUYAUTEUR_C'),          -- Plomberie → tuyauteur
    ('23', 'FERBLANTIER_C'),        -- CVAC → ferblantier (section 23 07 → CALORIFUGEUR_C)
    ('26', 'ELECTRICIEN_C'),        -- Électricité → électricien
    ('27', 'INSTALL_SECU_C'),       -- Communications → installateur systèmes sécurité
    ('31', 'OP_PELLES_AA_C')        -- Terrassement → opérateur de pelles AA
) AS v(div, code)
JOIN ad_budget.taux_horaires t ON t.code = v.code AND t.actif = TRUE
ON CONFLICT (csi_division) DO NOTHING;
