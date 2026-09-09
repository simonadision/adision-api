-- Ad BUD — les sous-totaux ne peuvent plus être autre chose qu'une LISTE
-- (9 septembre 2026, revue complète de la fonctionnalité « Sous-total »).
--
-- POURQUOI. Deux montants faux sont sortis d'affilée d'une soumission
-- CLIENT sur cette fonctionnalité — sous-totaux absents du PDF (PR #100),
-- puis 197 627 $ comptés six fois (PR #101). Simon, en direct : « C'est
-- très dangereux, si on envoie une soumission qui manquent des montants. »
-- Les deux fois, la donnée elle-même était acceptable pour la base et
-- n'est devenue fausse que chez UN lecteur. La correction de fond est
-- côté écriture (_normaliser_regroupements, modules/ad_gabarits_api.py) :
-- dédoublonnage des membres avant stockage, pour que la donnée soit saine
-- à la source plutôt que réparée par chaque lecteur indépendamment.
--
-- CE QUE CETTE MIGRATION AJOUTE, et ce qu'elle n'ajoute pas. Elle pose la
-- seule garantie que SQL sait tenir à moindre coût : la colonne est une
-- LISTE JSON, jamais un objet, jamais un nombre, jamais une chaîne. Un
-- futur chemin d'écriture qui contournerait la normalisation Python
-- (script ponctuel, correctif à la main, nouvelle route) se fait refuser
-- au lieu de laisser un budget que plus aucun écran ne sait afficher :
-- tous les lecteurs itèrent cette colonne, un objet les fait tomber.
-- Elle NE valide PAS le contenu de chaque entrée (nom, membres,
-- dédoublonnage) : l'exprimer en CHECK coûterait une fonction SQL à tenir
-- en phase avec le Python — une quatrième copie de la règle, exactement
-- le défaut qu'on corrige. Le contenu reste la charge de
-- _normaliser_regroupements, seul point d'écriture.
--
-- RÉPARATION PRÉALABLE. Une ligne du mauvais type JSON est déjà illisible
-- par tous les consommateurs (ils font `.map` sur la liste) : la remettre
-- à '[]' ne perd aucun affichage existant, et sans cela l'ALTER échouerait
-- au démarrage et emporterait le service entier. Les identifiants touchés
-- sont annoncés en NOTICE plutôt que réparés en silence. En pratique zéro
-- ligne est attendue : la colonne est NOT NULL DEFAULT '[]' et tous les
-- chemins d'écriture passent par json.dumps(list).
--
-- Idempotente (garde sur pg_constraint, cf. noyau §4.1bis) et rejouable
-- depuis une base vide. Ordre alphabétique du runner : « sprint_r… » passe
-- après « sprint_gabarits_regroupements » et « sprint_projet_regroupements »,
-- qui créent les deux colonnes.

DO $$
DECLARE
  mauvais_projets TEXT;
  mauvais_gabarits TEXT;
BEGIN
  SELECT string_agg(id::text, ', ') INTO mauvais_projets
    FROM ad_budget.projets WHERE jsonb_typeof(regroupements) <> 'array';
  IF mauvais_projets IS NOT NULL THEN
    RAISE NOTICE 'regroupements non-liste remis a [] — ad_budget.projets id: %', mauvais_projets;
    UPDATE ad_budget.projets SET regroupements = '[]'::jsonb
      WHERE jsonb_typeof(regroupements) <> 'array';
  END IF;

  SELECT string_agg(id::text, ', ') INTO mauvais_gabarits
    FROM ad_budget.gabarits WHERE jsonb_typeof(regroupements) <> 'array';
  IF mauvais_gabarits IS NOT NULL THEN
    RAISE NOTICE 'regroupements non-liste remis a [] — ad_budget.gabarits id: %', mauvais_gabarits;
    UPDATE ad_budget.gabarits SET regroupements = '[]'::jsonb
      WHERE jsonb_typeof(regroupements) <> 'array';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'projets_regroupements_est_liste'
  ) THEN
    ALTER TABLE ad_budget.projets
      ADD CONSTRAINT projets_regroupements_est_liste
      CHECK (jsonb_typeof(regroupements) = 'array');
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'gabarits_regroupements_est_liste'
  ) THEN
    ALTER TABLE ad_budget.gabarits
      ADD CONSTRAINT gabarits_regroupements_est_liste
      CHECK (jsonb_typeof(regroupements) = 'array');
  END IF;
END $$;
