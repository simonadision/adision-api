-- ════════════════════════════════════════════════════════════════════
-- Ad BUD — VERSIONS DÉBALANCÉES : des facteurs, jamais une copie
-- ════════════════════════════════════════════════════════════════════
-- Simon, 5 oct. 2026 14 h 37 : « les versions débalancées existent en
-- VERSION… et doivent être accessibles dans AD CON EN TEMPS RÉEL… donc vivent
-- dans Ad BUD, Ad EST (si créé comme projet dans Ad EST) et Ad CON. »
-- Et, à 14 h 36 : « **Rien ne peut toucher à l'original.** »
--
-- ON NE COPIE PAS LES LIGNES, ET C'EST « TEMPS RÉEL » QUI L'IMPOSE. Une copie
-- figée serait fausse dès la première correction de prix dans l'original :
-- Ad CON montrerait un budget périmé en croyant le lire en direct. La version
-- stocke donc un FACTEUR par ligne, et ses montants se recalculent à la
-- lecture : `montant_version = total(ligne) × facteur`. Simon corrige une
-- ligne, la version suit sans que personne refasse quoi que ce soit.
--
-- LE FACTEUR, JAMAIS L'AJUSTEMENT RÉSULTANT. Stocker un `ajustement_pct`
-- calculé le rendrait faux dès que celui de l'original change — et personne
-- ne le verrait. Le facteur, lui, se compose correctement avec ce que la
-- ligne porte au moment de la lecture. C'est aussi ce que produit
-- `appliquerVersion` côté écran : on ne convertit pas pour stocker puis
-- reconvertit pour lire.
--
-- `total_base` : LA MÉMOIRE DE CE QUE LA LIGNE VALAIT AU DÉBALANCEMENT.
-- Sans lui, on peut constater qu'une version ne balance plus mais pas NOMMER
-- pourquoi — et un écart sans cause est pire qu'un écart. Avec lui :
-- « 3 lignes ont changé depuis ».
--
-- ⚠ LA BASE EST CELLE DU SERVEUR, PAS CELLE DU CLIENT. Elle est lue sous
-- `FOR UPDATE` dans la transaction qui écrit. L'aperçu de l'écran peut avoir
-- plusieurs minutes — un autre poste a pu modifier une ligne entre-temps — et
-- enregistrer SA base ferait calculer la dérive contre une fiction. La base
-- envoyée par le client ne sert qu'à le CONTRÔLER : si elle diffère, son
-- aperçu était périmé et on refuse (409) plutôt que d'enregistrer une version
-- fondée sur ce que Simon ne voyait déjà plus.
--
-- LA DÉRIVE SERA LE QUOTIDIEN, PAS L'EXCEPTION. Ligne à 100 $ avec facteur
-- 1,25 ; le prix passe à 200 $ : l'original monte de 100, la version de 125,
-- écart +25 $. Aucune ligne ajoutée, aucun facteur orphelin. **Toute
-- modification d'une ligne débalancée fait dériver sa version.** D'où :
--   * on refuse d'ENREGISTRER une version qui ne balance pas (409) ;
--   * on ne refuse JAMAIS d'en LIRE une qui a dérivé — on la sert avec son
--     écart. Appliquer le seuil à la lecture ferait cesser Ad CON d'afficher
--     un budget parce qu'il a dérivé de 25 $.
--
-- JSONB ET NON UNE TABLE FILLE : les facteurs s'écrivent en une fois, se
-- lisent en une fois, et ne sont jamais interrogés ligne par ligne. Une table
-- fille coûterait un INSERT par ligne — 1 209 sur le plus gros budget — pour
-- une donnée qu'on ne requête jamais seule.
--
-- SUPPRESSION DOUCE : un rapport ÉMIS peut pointer sur une version. Le GET
-- doit continuer à la servir, avec `supprimee_le`, pour qu'une réimpression
-- ressorte la MÊME chose et le DISE (« version X, supprimée depuis le … »).
-- Un DELETE dur ferait mentir une réimpression, en silence.
--
-- IDEMPOTENTE. Suivi par nom de fichier dans `ad_budget.schema_migrations`.
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ad_budget.versions_debalancees (
    id            BIGSERIAL PRIMARY KEY,
    projet_id     INTEGER NOT NULL
                    REFERENCES ad_budget.projets(id) ON DELETE CASCADE,
    nom           TEXT NOT NULL,
    cree_le       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cree_par      TEXT,
    maj_le        TIMESTAMPTZ,
    maj_par       TEXT,
    -- Ce que la personne a DEMANDÉ, en clair : { "<rangée>": 12500.00 }.
    -- Pour qu'une version d'il y a trois mois reste lisible.
    cibles        JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- { "mode": "prorata" } | { "mode": "manuel", "parRangee": {…} }
    compensation  JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Où le geste a été fait : « detaille », « sommaire:division », …
    affichage     TEXT,
    -- { "<ligne_id>": { "facteur": 1.25, "base": 100.00 } } — voir l'en-tête.
    facteurs      JSONB NOT NULL DEFAULT '{}'::jsonb,
    nb_facteurs   INTEGER NOT NULL DEFAULT 0,
    supprimee_le  TIMESTAMPTZ,
    supprimee_par TEXT
);

-- Deux versions d'un même budget ne portent pas le même nom : Simon choisit
-- parmi elles dans un sélecteur, et deux « Débalancée » ne se distingueraient
-- pas. On ne compte PAS les supprimées — un nom libéré doit pouvoir resservir.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_version_nom_par_projet
    ON ad_budget.versions_debalancees (projet_id, lower(nom))
    WHERE supprimee_le IS NULL;

-- « Les versions de ce projet », la seule lecture de liste.
CREATE INDEX IF NOT EXISTS idx_versions_debalancees_projet
    ON ad_budget.versions_debalancees (projet_id, cree_le DESC)
    WHERE supprimee_le IS NULL;

COMMENT ON TABLE ad_budget.versions_debalancees IS
  'Versions débalancées d''un budget (5 oct 2026) : des FACTEURS par ligne, '
  'jamais une copie. Les montants se recalculent à la lecture, donc la '
  'version suit l''original en temps réel. L''original n''est jamais écrit.';

COMMENT ON COLUMN ad_budget.versions_debalancees.facteurs IS
  '{ "<ligne_id>": { "facteur": n, "base": n } }. `base` = ce que la ligne '
  'valait AU DÉBALANCEMENT, lu par le SERVEUR sous FOR UPDATE — il sert à '
  'nommer la cause d''une dérive (« n lignes ont changé depuis »).';

DO $$
DECLARE
    presente BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'ad_budget' AND table_name = 'versions_debalancees'
    ) INTO presente;
    RAISE NOTICE '[versions_debalancees] table presente : % (attendu true).', presente;
END $$;
