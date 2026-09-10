-- Ad BUD — Cinquième statut de projet : « en exécution » (10 septembre 2026).
--
-- Demande de Simon : « je veux ajouter un statut execution ». Pendant du
-- changement côté Ad HUB (migration 177) : le vocabulaire des statuts est
-- PARTAGÉ entre les deux produits (cf. ALLOWED_STATUTS / STATUTS), et une
-- valeur acceptée d'un côté mais refusée de l'autre casserait la synchro dès
-- qu'un projet la porterait.
--
-- POURQUOI IL MANQUAIT. Le regroupement de neuf statuts à quatre, le matin
-- même, a fondu DEUX moments distincts dans « en cours » : le projet qu'on
-- CHIFFRE et le chantier qu'on CONSTRUIT. Le cinquième sépare ce que ce
-- regroupement avait recollé de trop — il ne ressuscite pas les neuf.
--
-- ORDRE DES OPÉRATIONS, ET C'EST LA LEÇON DU 9 SEPTEMBRE (Ad BUD ET Ad HUB
-- tombés le même après-midi, à une heure d'intervalle) : on DROP la contrainte
-- AVANT de la recréer, en nommant TOUTES les variantes.
--
-- Ici il y en a TROIS, et la troisième est la piégeuse : `projet_statut_check`
-- au SINGULIER, nom auto-généré du temps où la table s'appelait `projet`. Elle
-- a survécu au renommage, `IF EXISTS` sur les deux autres noms l'a laissée en
-- place SANS RIEN DIRE, et c'est elle qui a fait tomber l'API. On la nomme.
--
-- Les deux colonnes sont traitées : `statut` (état métier) et
-- `categorie_affichage` (pastilles de tri de la page Projets), qui partagent
-- le même vocabulaire depuis le regroupement.
--
-- Aucune donnée modifiée : on élargit une liste, on n'en retire rien.

-- ── statut ────────────────────────────────────────────────────────────────
ALTER TABLE ad_budget.projets DROP CONSTRAINT IF EXISTS projets_statut_chk;
ALTER TABLE ad_budget.projets DROP CONSTRAINT IF EXISTS projets_statut_check;
ALTER TABLE ad_budget.projets DROP CONSTRAINT IF EXISTS projet_statut_check;

ALTER TABLE ad_budget.projets
  ADD CONSTRAINT projets_statut_chk
  CHECK (statut IN ('en_soumission', 'en_cours', 'en_execution', 'perdu', 'archive'));

-- ── categorie_affichage ───────────────────────────────────────────────────
ALTER TABLE ad_budget.projets DROP CONSTRAINT IF EXISTS projets_categorie_affichage_chk;
ALTER TABLE ad_budget.projets DROP CONSTRAINT IF EXISTS projets_categorie_affichage_check;
ALTER TABLE ad_budget.projets DROP CONSTRAINT IF EXISTS projet_categorie_affichage_check;

ALTER TABLE ad_budget.projets
  ADD CONSTRAINT projets_categorie_affichage_chk
  CHECK (categorie_affichage IS NULL
         OR categorie_affichage IN ('en_soumission', 'en_cours', 'en_execution', 'perdu', 'archive'));
