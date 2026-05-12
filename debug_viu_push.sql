-- ════════════════════════════════════════════════════════════════════════════
-- Debug push Ad VIU -> Ad BUD : doublons observes sur projet 93 / analyse #18
-- A coller dans Railway Data tab > adision-api > Run query
-- Tous les queries scope explicite projet 93 — ajuste si tu testes ailleurs.
-- ════════════════════════════════════════════════════════════════════════════

-- (1) Total + breakdown par type_source. Devrait montrer 76 lignes :
--     25 NULL (squelette manuel blindspot) + 51 'viu_v2' (items Ad VIU push #2)
SELECT
  COUNT(*)                                     AS total,
  COUNT(*) FILTER (WHERE type_source = 'viu_v2') AS viu_v2_count,
  COUNT(*) FILTER (WHERE type_source IS NULL)    AS null_count,
  COUNT(*) FILTER (
    WHERE type_source IS NOT NULL
      AND type_source <> 'viu_v2'
  )                                              AS other_count
FROM ad_budget.budget_lignes
WHERE projet_id = 93;


-- (2) DISTINCT par (section, type_source) — voir si une section a 2+ lignes
--     dans le MEME type_source. Si oui = doublons confirmes.
SELECT section, type_source, COUNT(*) AS n
FROM ad_budget.budget_lignes
WHERE projet_id = 93
GROUP BY section, type_source
HAVING COUNT(*) > 1
ORDER BY section, type_source;


-- (3) Detection doublons CONTENU : meme (section, description) apparait 2+ fois ?
--     C'est l'hypothese principale apres fix Option C (Ad VIU enverrait 2 items
--     identiques dans le payload).
SELECT
  section,
  description,
  type_source,
  COUNT(*)              AS occurrences,
  ARRAY_AGG(id)         AS line_ids,
  ARRAY_AGG(source_viu_item_id) AS viu_item_ids,
  ARRAY_AGG(qte)        AS quantites
FROM ad_budget.budget_lignes
WHERE projet_id = 93
GROUP BY section, description, type_source
HAVING COUNT(*) > 1
ORDER BY occurrences DESC, section
LIMIT 50;


-- (4) Smoking gun : lignes NON-blindspot ET NON-viu_v2 qui auraient DU etre
--     deleted par DELETE Option C. Devrait retourner 0 rows. Si > 0 = bug
--     de filtre.
SELECT
  id,
  section,
  type_source,
  qte,
  description,
  LEFT(REGEXP_REPLACE(COALESCE(section, ''), '\D', '', 'g'), 2) AS division_extracted,
  source_viu_analysis_id,
  source_viu_item_id
FROM ad_budget.budget_lignes
WHERE projet_id = 93
  AND LEFT(REGEXP_REPLACE(COALESCE(section, ''), '\D', '', 'g'), 2)
      NOT IN ('01','20','21','22','23','25','26','27','28','31','32','33')
  AND (type_source IS NULL OR type_source <> 'viu_v2')
ORDER BY section;


-- (5) Sample de chaque section + division extraite, pour valider que la
--     normalisation regex extrait bien les 2 chiffres attendus.
SELECT
  section,
  LEFT(REGEXP_REPLACE(COALESCE(section, ''), '\D', '', 'g'), 2) AS division_extracted,
  COUNT(*) AS n_lines,
  STRING_AGG(DISTINCT type_source, ', ') AS type_sources
FROM ad_budget.budget_lignes
WHERE projet_id = 93
GROUP BY section
ORDER BY section;


-- (6) Si on suspecte des items Ad VIU avec des source_viu_item_id NULL
--     (qui n'auraient pas ete deduplique par le filtre source_viu_item_id de
--     l'ancien code) :
SELECT
  COUNT(*) AS viu_items_with_null_id
FROM ad_budget.budget_lignes
WHERE projet_id = 93
  AND type_source = 'viu_v2'
  AND source_viu_item_id IS NULL;
