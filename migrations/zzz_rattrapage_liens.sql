-- ============================================================
-- RATTRAPAGE - Ad BUD - schemas ad_budget et app_ana (queue)
--
-- Genere le 2026-08-07 par lecture du schema REEL de la production.
-- Constate, ne modifie pas : chaque instruction est gardee, donc rejouee
-- sur la production elle ne fait rien. Aucun DROP, aucun DELETE.
--
-- Raison d'etre : ces objets existaient en production sans qu'aucun
-- fichier du depot ne les cree. Une installation neuve etait impossible.
-- ============================================================

-- Le service resout les noms courts par son search_path ; on le pose
-- ici pour que le rejeu sur base neuve resolve pareil. LOCAL : borne
-- a cette transaction, donc a ce seul fichier.
SET LOCAL search_path TO "ad_budget", "app_ana", public;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'budget_lignes'
                   AND c.conname = 'budget_lignes_projet_id_fkey') THEN
    ALTER TABLE "ad_budget"."budget_lignes" ADD CONSTRAINT "budget_lignes_projet_id_fkey" FOREIGN KEY (projet_id) REFERENCES projets(id) ON DELETE CASCADE;
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'devis'
                   AND c.conname = 'devis_projet_id_fkey') THEN
    ALTER TABLE "ad_budget"."devis" ADD CONSTRAINT "devis_projet_id_fkey" FOREIGN KEY (projet_id) REFERENCES projets(id) ON DELETE CASCADE;
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'projets'
                   AND c.conname = 'projet_dernier_snapshot_fk') THEN
    ALTER TABLE "ad_budget"."projets" ADD CONSTRAINT "projet_dernier_snapshot_fk" FOREIGN KEY (dernier_snapshot_id) REFERENCES project_snapshots(id) ON DELETE SET NULL;
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'projets'
                   AND c.conname = 'projets_source_gabarit_fk') THEN
    ALTER TABLE "ad_budget"."projets" ADD CONSTRAINT "projets_source_gabarit_fk" FOREIGN KEY (source_gabarit_id) REFERENCES gabarits(id) ON DELETE SET NULL;
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'projets'
                   AND c.conname = 'projets_user_id_fkey') THEN
    ALTER TABLE "ad_budget"."projets" ADD CONSTRAINT "projets_user_id_fkey" FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'user_gabarit_defaut'
                   AND c.conname = 'user_gabarit_defaut_gabarit_id_fkey') THEN
    ALTER TABLE "ad_budget"."user_gabarit_defaut" ADD CONSTRAINT "user_gabarit_defaut_gabarit_id_fkey" FOREIGN KEY (gabarit_id) REFERENCES gabarits(id) ON DELETE CASCADE;
  END IF;
END $rattrapage$;

CREATE INDEX IF NOT EXISTS idx_bud_lignes_item_mat ON ad_budget.budget_lignes USING btree (item_id_ad_mat) WHERE (item_id_ad_mat IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_budget_lignes_viu_item ON ad_budget.budget_lignes USING btree (source_viu_item_id) WHERE (source_viu_item_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_projet_statut ON ad_budget.projets USING btree (statut);

CREATE INDEX IF NOT EXISTS idx_projets_ad_hub_project_id ON ad_budget.projets USING btree (ad_hub_project_id) WHERE (ad_hub_project_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_projets_client_id ON ad_budget.projets USING btree (client_id) WHERE (client_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_projets_organization ON ad_budget.projets USING btree (organization_id);

CREATE INDEX IF NOT EXISTS projets_source_gabarit_idx ON ad_budget.projets USING btree (source_gabarit_id) WHERE (source_gabarit_id IS NOT NULL);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_budget_actif_par_hub ON ad_budget.projets USING btree (ad_hub_project_id) WHERE (((statut)::text <> 'archive'::text) AND (ad_hub_project_id IS NOT NULL));

CREATE INDEX IF NOT EXISTS idx_user_gabarit_defaut_org ON ad_budget.user_gabarit_defaut USING btree (organization_id);

CREATE INDEX IF NOT EXISTS idx_snapshots_aggregates_gin ON app_ana.project_snapshots USING gin (aggregates_jsonb);

CREATE INDEX IF NOT EXISTS idx_snapshots_client_nom ON app_ana.project_snapshots USING btree (client_nom);

CREATE INDEX IF NOT EXISTS idx_snapshots_date_adjudication ON app_ana.project_snapshots USING btree (date_adjudication);

CREATE INDEX IF NOT EXISTS idx_snapshots_is_latest ON app_ana.project_snapshots USING btree (is_latest) WHERE (is_latest = true);

CREATE INDEX IF NOT EXISTS idx_snapshots_projet_id ON app_ana.project_snapshots USING btree (projet_id);

CREATE INDEX IF NOT EXISTS idx_snapshots_region ON app_ana.project_snapshots USING btree (region);

CREATE INDEX IF NOT EXISTS idx_snapshots_statut ON app_ana.project_snapshots USING btree (statut);

CREATE INDEX IF NOT EXISTS idx_snapshots_type_batiment ON app_ana.project_snapshots USING btree (type_batiment);
