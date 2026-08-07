-- ============================================================
-- RATTRAPAGE - Ad BUD - schemas ad_budget et app_ana (socle)
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

CREATE SCHEMA IF NOT EXISTS "ad_budget";

CREATE SCHEMA IF NOT EXISTS "app_ana";

CREATE SEQUENCE IF NOT EXISTS "ad_budget"."ad_budget_prix_moyens_id_seq";

CREATE SEQUENCE IF NOT EXISTS "ad_budget"."budget_lignes_id_seq";

CREATE SEQUENCE IF NOT EXISTS "ad_budget"."gabarits_id_seq";

CREATE SEQUENCE IF NOT EXISTS "ad_budget"."projets_id_seq";

CREATE SEQUENCE IF NOT EXISTS "ad_budget"."users_id_seq";

CREATE SEQUENCE IF NOT EXISTS "app_ana"."project_snapshots_id_seq";

CREATE TABLE IF NOT EXISTS "ad_budget"."ad_budget_prix_moyens" (
  "id" integer DEFAULT nextval('ad_budget.ad_budget_prix_moyens_id_seq'::regclass) NOT NULL,
  "section" text,
  "division" text,
  "description" text,
  "unite" text,
  "note" text,
  "surface_pieds" numeric,
  "ajustement" numeric,
  "prix_unitaire" numeric,
  "source_fichier" text,
  "annee" integer,
  "created_at" timestamp without time zone DEFAULT now(),
  "updated_at" timestamp without time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS "ad_budget"."budget_lignes" (
  "id" integer DEFAULT nextval('ad_budget.budget_lignes_id_seq'::regclass) NOT NULL,
  "projet_id" integer NOT NULL,
  "source_item_id" integer,
  "section" character varying(100),
  "description" character varying(300),
  "unite" character varying(50),
  "prix_unitaire" numeric(12,4) DEFAULT 0,
  "qte" numeric(12,4) DEFAULT 0,
  "ajustement_pct" numeric(8,4) DEFAULT 0,
  "note" text,
  "actif" boolean DEFAULT true NOT NULL,
  "sous_total" numeric(12,2) GENERATED ALWAYS AS ((qte * prix_unitaire)) STORED,
  "total" numeric(12,2) GENERATED ALWAYS AS (((qte * prix_unitaire) * ((1)::numeric + (ajustement_pct / (100)::numeric)))) STORED,
  "created_at" timestamp without time zone DEFAULT now() NOT NULL,
  "updated_at" timestamp without time zone DEFAULT now() NOT NULL,
  "source_file" text,
  "type_source" text,
  "heures" numeric(10,2) DEFAULT 0,
  "taux_horaire" numeric(10,2) DEFAULT 0,
  "cout_sous_traitant" numeric(12,2) DEFAULT 0,
  "sous_traitant_nom" text,
  "ajust_materiaux" numeric(5,2) DEFAULT 0,
  "ajust_main_oeuvre" numeric(5,2) DEFAULT 0,
  "ajust_sous_traitant" numeric(5,2) DEFAULT 0,
  "sous_traitant_type" text,
  "sous_traitant_montant" numeric(12,2) DEFAULT 0,
  "source_viu_analysis_id" integer,
  "source_viu_item_id" integer,
  "item_id_ad_mat" integer,
  "ad_hub_pending_id" integer,
  "sous_traitant_contact_id" uuid,
  "source_typ_code" text,
  "source_typ_snapshot_at" timestamp with time zone,
  "prix_unitaire_override" boolean DEFAULT false NOT NULL,
  "heures_manuelles" boolean DEFAULT false NOT NULL,
  "item_ad_mat_scope" text,
  "source_mat_prix_snapshot" numeric,
  "source_mat_snapshot_at" timestamp with time zone,
  "qte_override" boolean DEFAULT false NOT NULL,
  "taux_horaire_override" boolean DEFAULT false NOT NULL,
  "ajust_materiaux_override" boolean DEFAULT false NOT NULL,
  "ajust_main_oeuvre_override" boolean DEFAULT false NOT NULL,
  "ajust_sous_traitant_override" boolean DEFAULT false NOT NULL,
  "sous_traitant_montant_override" boolean DEFAULT false NOT NULL,
  "prix_unitaire_st" numeric(14,4) DEFAULT 0 NOT NULL,
  "prix_unitaire_st_override" boolean DEFAULT false NOT NULL
);

CREATE TABLE IF NOT EXISTS "ad_budget"."devis" (
  "projet_id" integer NOT NULL,
  "presentation" text,
  "travaux_inclus" text,
  "travaux_non_inclus" text,
  "responsable_nom" text,
  "responsable_email" text,
  "responsable_tel" text,
  "documents" jsonb DEFAULT '[]'::jsonb NOT NULL,
  "couleur" text DEFAULT 'adision'::text NOT NULL,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL,
  "updated_at" timestamp with time zone DEFAULT now() NOT NULL,
  "titre_responsable" text,
  "organisation" text
);

CREATE TABLE IF NOT EXISTS "ad_budget"."gabarits" (
  "id" integer DEFAULT nextval('ad_budget.gabarits_id_seq'::regclass) NOT NULL,
  "organization_id" uuid NOT NULL,
  "nom" character varying(200) NOT NULL,
  "description" text,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL,
  "updated_at" timestamp with time zone DEFAULT now() NOT NULL,
  "est_master" boolean DEFAULT false NOT NULL,
  "source_master_gabarit_id" integer,
  "push_id" uuid,
  "created_by" integer,
  "created_by_email" character varying(255)
);

CREATE TABLE IF NOT EXISTS "ad_budget"."projets" (
  "id" integer DEFAULT nextval('ad_budget.projets_id_seq'::regclass) NOT NULL,
  "user_id" integer NOT NULL,
  "statut" character varying(50) DEFAULT 'brouillon'::character varying NOT NULL,
  "created_at" timestamp without time zone DEFAULT now() NOT NULL,
  "updated_at" timestamp without time zone DEFAULT now() NOT NULL,
  "notes" text DEFAULT ''::text NOT NULL,
  "pct_admin_conditions" numeric(6,3) DEFAULT 0 NOT NULL,
  "pct_admin_architecture" numeric(6,3) DEFAULT 0 NOT NULL,
  "pct_admin_mecanique" numeric(6,3) DEFAULT 0 NOT NULL,
  "pct_admin_excavation" numeric(6,3) DEFAULT 0 NOT NULL,
  "dernier_snapshot_id" integer,
  "mobilisation" numeric(10,2),
  "surface_plancher" numeric(10,2),
  "hauteur_cloisons" numeric(10,2),
  "longueur_cloisons" numeric(10,2),
  "organization_id" uuid,
  "ad_hub_project_id" integer,
  "client_id" bigint,
  "pct_admin_conditions_mat" numeric,
  "pct_admin_conditions_mo" numeric,
  "pct_admin_conditions_st" numeric,
  "pct_admin_architecture_mat" numeric,
  "pct_admin_architecture_mo" numeric,
  "pct_admin_architecture_st" numeric,
  "pct_admin_mecanique_mat" numeric,
  "pct_admin_mecanique_mo" numeric,
  "pct_admin_mecanique_st" numeric,
  "pct_admin_excavation_mat" numeric,
  "pct_admin_excavation_mo" numeric,
  "pct_admin_excavation_st" numeric,
  "arrondi_dollar" boolean DEFAULT true NOT NULL,
  "revision_no" integer DEFAULT 0 NOT NULL,
  "revision_label" text,
  "emitted_at_current_revision" boolean DEFAULT false NOT NULL,
  "budget_modifie_depuis_emission" boolean DEFAULT false NOT NULL,
  "is_verrouille" boolean DEFAULT false NOT NULL,
  "verrouille_par" text,
  "verrouille_le" timestamp with time zone,
  "pct_admin_mode" text DEFAULT 'global'::text NOT NULL,
  "hub_identity_snapshot" jsonb,
  "hub_identity_snapshot_at" timestamp with time zone,
  "detenteur_email" text,
  "detenteur_id" integer,
  "detenteur_nom" text,
  "detenu_depuis" timestamp with time zone,
  "derniere_activite" timestamp with time zone,
  "source_gabarit_id" integer
);

CREATE TABLE IF NOT EXISTS "ad_budget"."user_gabarit_defaut" (
  "user_id" integer NOT NULL,
  "gabarit_id" integer NOT NULL,
  "organization_id" uuid NOT NULL,
  "updated_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS "ad_budget"."users" (
  "id" integer DEFAULT nextval('ad_budget.users_id_seq'::regclass) NOT NULL,
  "nom" character varying(100) NOT NULL,
  "email" character varying(150) NOT NULL,
  "role" character varying(20) DEFAULT 'user'::character varying NOT NULL,
  "created_at" timestamp without time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS "app_ana"."project_snapshots" (
  "id" integer DEFAULT nextval('app_ana.project_snapshots_id_seq'::regclass) NOT NULL,
  "projet_id" integer NOT NULL,
  "nom_projet" character varying(255),
  "client_nom" character varying(255),
  "statut" character varying(20) NOT NULL,
  "type_batiment" character varying(20),
  "region" character varying(50),
  "date_adjudication" date,
  "superficie_m2" numeric(10,2),
  "budget_lines_jsonb" jsonb NOT NULL,
  "aggregates_jsonb" jsonb NOT NULL,
  "created_at" timestamp without time zone DEFAULT now() NOT NULL,
  "trigger_event" character varying(50) NOT NULL,
  "schema_version" integer DEFAULT 1 NOT NULL,
  "is_latest" boolean DEFAULT true NOT NULL
);

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'ad_budget_prix_moyens'
                   AND c.conname = 'ad_budget_prix_moyens_pkey') THEN
    ALTER TABLE "ad_budget"."ad_budget_prix_moyens" ADD CONSTRAINT "ad_budget_prix_moyens_pkey" PRIMARY KEY (id);
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'budget_lignes'
                   AND c.conname = 'budget_lignes_pkey') THEN
    ALTER TABLE "ad_budget"."budget_lignes" ADD CONSTRAINT "budget_lignes_pkey" PRIMARY KEY (id);
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'devis'
                   AND c.conname = 'devis_pkey') THEN
    ALTER TABLE "ad_budget"."devis" ADD CONSTRAINT "devis_pkey" PRIMARY KEY (projet_id);
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'gabarits'
                   AND c.conname = 'gabarits_pkey') THEN
    ALTER TABLE "ad_budget"."gabarits" ADD CONSTRAINT "gabarits_pkey" PRIMARY KEY (id);
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'projets'
                   AND c.conname = 'projets_pkey') THEN
    ALTER TABLE "ad_budget"."projets" ADD CONSTRAINT "projets_pkey" PRIMARY KEY (id);
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'user_gabarit_defaut'
                   AND c.conname = 'user_gabarit_defaut_pkey') THEN
    ALTER TABLE "ad_budget"."user_gabarit_defaut" ADD CONSTRAINT "user_gabarit_defaut_pkey" PRIMARY KEY (user_id);
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'users'
                   AND c.conname = 'users_pkey') THEN
    ALTER TABLE "ad_budget"."users" ADD CONSTRAINT "users_pkey" PRIMARY KEY (id);
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'app_ana' AND r.relname = 'project_snapshots'
                   AND c.conname = 'project_snapshots_pkey') THEN
    ALTER TABLE "app_ana"."project_snapshots" ADD CONSTRAINT "project_snapshots_pkey" PRIMARY KEY (id);
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'users'
                   AND c.conname = 'users_email_key') THEN
    ALTER TABLE "ad_budget"."users" ADD CONSTRAINT "users_email_key" UNIQUE (email);
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'budget_lignes'
                   AND c.conname = 'ck_budget_lignes_mat_scope') THEN
    ALTER TABLE "ad_budget"."budget_lignes" ADD CONSTRAINT "ck_budget_lignes_mat_scope" CHECK (((item_ad_mat_scope IS NULL) OR (item_ad_mat_scope = ANY (ARRAY['master'::text, 'client'::text]))));
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'projets'
                   AND c.conname = 'projet_hauteur_cloisons_check') THEN
    ALTER TABLE "ad_budget"."projets" ADD CONSTRAINT "projet_hauteur_cloisons_check" CHECK (((hauteur_cloisons IS NULL) OR (hauteur_cloisons >= (0)::numeric)));
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'projets'
                   AND c.conname = 'projet_longueur_cloisons_check') THEN
    ALTER TABLE "ad_budget"."projets" ADD CONSTRAINT "projet_longueur_cloisons_check" CHECK (((longueur_cloisons IS NULL) OR (longueur_cloisons >= (0)::numeric)));
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'projets'
                   AND c.conname = 'projet_mobilisation_check') THEN
    ALTER TABLE "ad_budget"."projets" ADD CONSTRAINT "projet_mobilisation_check" CHECK (((mobilisation IS NULL) OR (mobilisation >= (0)::numeric)));
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'projets'
                   AND c.conname = 'projet_statut_check') THEN
    ALTER TABLE "ad_budget"."projets" ADD CONSTRAINT "projet_statut_check" CHECK (((statut)::text = ANY ((ARRAY['brouillon'::character varying, 'adjuge'::character varying, 'complet'::character varying, 'perdu'::character varying, 'archive'::character varying])::text[])));
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'projets'
                   AND c.conname = 'projet_surface_plancher_check') THEN
    ALTER TABLE "ad_budget"."projets" ADD CONSTRAINT "projet_surface_plancher_check" CHECK (((surface_plancher IS NULL) OR (surface_plancher >= (0)::numeric)));
  END IF;
END $rattrapage$;

DO $rattrapage$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                 JOIN pg_class r ON r.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = r.relnamespace
                 WHERE n.nspname = 'ad_budget' AND r.relname = 'projets'
                   AND c.conname = 'projets_pct_admin_mode_chk') THEN
    ALTER TABLE "ad_budget"."projets" ADD CONSTRAINT "projets_pct_admin_mode_chk" CHECK ((pct_admin_mode = ANY (ARRAY['global'::text, 'ventile'::text])));
  END IF;
END $rattrapage$;
