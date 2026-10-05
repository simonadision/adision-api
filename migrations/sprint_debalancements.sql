-- ════════════════════════════════════════════════════════════════════
-- Ad BUD — DÉBALANCEMENT : la photo qui rend l'annulation possible
-- ════════════════════════════════════════════════════════════════════
-- Simon, 5 oct 2026 13 h 51 : « option débalancement… une colonne cible à
-- atteindre. Ex. 10 000 en électricité, cible 12 500 = +2 500, mais le grand
-- total reste le même. […] réduire chacune des lignes au prorata ».
--
-- POURQUOI CETTE TABLE EXISTE, ET C'EST LA SEULE RAISON. Le débalancement
-- écrit un facteur DANS `budget_lignes.ajustement_pct` — la colonne où Simon
-- tape lui-même ses ajustements. Sans photo préalable, la nouvelle valeur est
-- une COMBINAISON : plus rien ne distingue ce qu'il avait saisi de ce que le
-- débalancement a posé. **Il ne pourrait revenir qu'à zéro, en perdant son
-- travail.** La conception d'origine disait « tout se retire en remettant les
-- ajustements » ; c'est faux, et c'est ce que cette table répare.
--
-- `lignes` PORTE DEUX VALEURS PAR LIGNE, ET PAS UNE :
--     { "<ligne_id>": { "avant": 2.5000, "pose": 37.5000 } }
--   * `avant` sert à RESTAURER ;
--   * `pose` sert à DÉTECTER qu'on peut restaurer sans écraser personne.
-- À l'annulation, on compare la valeur ACTUELLE à `pose` : si elle diffère,
-- quelqu'un a touché cette ligne entre-temps — **on la saute et on la
-- NOMME**. Une annulation silencieusement partielle est pire qu'un refus.
-- Garder seulement `avant` rendrait cette détection impossible.
--
-- JSONB ET NON UNE TABLE FILLE : la photo est écrite en une fois, lue en une
-- fois, et n'est jamais interrogée ligne par ligne. Une table fille
-- coûterait un INSERT par ligne — 1 209 sur le plus gros budget — pour une
-- donnée qu'on ne requête jamais.
--
-- UNE ANNULATION EST UN COUP UNIQUE : `annule_le` est posé dès la tentative,
-- réussie, partielle ou non. Un deuxième essai restaurerait `avant` par-dessus
-- des lignes re-modifiées depuis.
--
-- ON NE SUPPRIME RIEN AVEC LE PROJET : `ON DELETE CASCADE` sur `projets`,
-- parce qu'un historique de débalancement sans son projet ne veut rien dire
-- et qu'il ne porte aucune donnée récupérable (la photo ne sert qu'à annuler
-- sur des lignes qui n'existent plus).
--
-- IDEMPOTENTE : `IF NOT EXISTS` partout. Ce dépôt suit ses migrations par nom
-- de fichier dans `ad_budget.schema_migrations`.
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ad_budget.debalancements (
    id              BIGSERIAL PRIMARY KEY,
    projet_id       INTEGER NOT NULL
                      REFERENCES ad_budget.projets(id) ON DELETE CASCADE,
    cree_le         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cree_par        TEXT,
    -- Ce que la personne a DEMANDÉ, en clair, pour qu'un débalancement d'il y
    -- a trois mois reste lisible : { "<rangée>": 12500.00 }.
    cibles          JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- { "mode": "prorata" } ou { "mode": "manuel", "parRangee": {…} }.
    compensation    JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Où le geste a été fait : « detaille », « sommaire:division »,
    -- « sommaire:division:<lot> ». Texte libre, pour l'historique seulement.
    affichage       TEXT,
    -- { "<ligne_id>": { "avant": n, "pose": n } } — voir l'en-tête.
    lignes          JSONB NOT NULL DEFAULT '{}'::jsonb,
    nb_lignes       INTEGER NOT NULL DEFAULT 0,
    annule_le       TIMESTAMPTZ,
    annule_par      TEXT,
    -- Trace de ce que l'annulation a pu faire : { "restaurees": n,
    -- "sautees": [ { "ligne_id": …, "description": … } ] }. Sert à redire la
    -- même chose si Simon redemande plus tard pourquoi une ligne n'a pas bougé.
    annulation      JSONB
);

-- « Le dernier débalancement de ce projet » est la SEULE lecture de cette
-- table, et elle sert la règle « on n'annule que le dernier ».
CREATE INDEX IF NOT EXISTS idx_debalancements_projet_date
    ON ad_budget.debalancements (projet_id, cree_le DESC);

COMMENT ON TABLE ad_budget.debalancements IS
  'Photo des ajustements AVANT chaque débalancement (5 oct 2026), pour que '
  '« Annuler » restaure exactement et n''écrase pas une saisie faite depuis.';

COMMENT ON COLUMN ad_budget.debalancements.lignes IS
  '{ "<ligne_id>": { "avant": n, "pose": n } }. `avant` restaure ; `pose` '
  'détecte qu''on peut restaurer sans écraser quelqu''un.';

DO $$
DECLARE
    presente BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'ad_budget' AND table_name = 'debalancements'
    ) INTO presente;
    RAISE NOTICE '[debalancements] table presente : % (attendu true).', presente;
END $$;
