-- Ad BUD — DEVIS (lettre de proposition qualitative), distinct du rapport.
--
-- 1 devis par projet (PK = projet_id). Champs ÉDITABLES persistés pour rééditer
-- le devis plus tard. Le reste (entreprise, client, montant, logo) est auto-rempli
-- à la génération (sources HUB/budget), pas stocké ici.
--
-- Tracké schema_migrations (joué une fois). IF NOT EXISTS par sécurité.

CREATE TABLE IF NOT EXISTS ad_budget.devis (
  projet_id           INTEGER PRIMARY KEY
                        REFERENCES ad_budget.projets(id) ON DELETE CASCADE,
  presentation        TEXT,
  travaux_inclus      TEXT,
  travaux_non_inclus  TEXT,
  -- Responsable (estimateur) : pré-rempli avec l'utilisateur connecté mais ÉDITABLE.
  responsable_nom     TEXT,
  responsable_email   TEXT,
  responsable_tel     TEXT,
  -- Documents GED cochés : liste de noms (l'affichage du devis liste ces noms).
  documents           JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- Mode couleur du PDF : 'adision' (bleu) | 'nb' (noir et blanc).
  couleur             TEXT NOT NULL DEFAULT 'adision',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
