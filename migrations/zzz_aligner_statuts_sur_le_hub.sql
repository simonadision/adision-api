-- ════════════════════════════════════════════════════════════════════
-- Ad BUD — Aligner les statuts sur le HUB, qui fait foi.
-- ════════════════════════════════════════════════════════════════════
-- Simon, 1er oct 2026 : « Tout est reproduit comme dans le hub, le hub est la
-- source » (20:01), « Hub est la vérité » (20:08), « Tu SYNC Bud avec hub »
-- (20:12), et son autorisation explicite d'écrire ceci (20:14).
--
-- CE QU'ON A MESURÉ. Sur les 23 projets Ad BUD liés à un projet hub,
-- 17 portaient un statut DIFFÉRENT de celui du hub. Le plus grave :
-- « Rénovation intérieure d'unités de logements » (budget 290, hub 771), son
-- chantier de 3 M$ — le hub le sait GAGNÉ, Ad BUD le croyait encore en
-- soumission, alors que son chargé de projet et son contremaître y
-- travaillent depuis des semaines.
--
-- POURQUOI ÇA A DÉRIVÉ : rien ne faisait descendre le statut du hub vers
-- Ad BUD. Tous les liens vont dans l'autre sens — Ad BUD POUSSE ses
-- instantanés vers le hub, et le bouton « ↻ Resync Ad HUB » lui-même ne fait
-- que re-pousser. Le sens descendant n'a été posé que le 1er octobre au soir
-- (adision-app-api #100 : le hub appelle Ad BUD à chacun de ses trois points
-- d'écriture du statut). Il couvre les changements FUTURS. Les 17 écarts
-- déjà là, non. Ce fichier les rattrape, une fois.
--
-- CE QUE ÇA RÉPARE EN AVAL. La photo Ad ANA se déclenche sur un changement de
-- statut DANS AD BUD. Ad BUD n'ayant jamais appris que ces projets étaient
-- gagnés, il n'a jamais figé leur photo. On a longtemps cru qu'« aucun projet
-- n'avait été gagné depuis le 21 septembre » ; la vérité est qu'Ad BUD ne le
-- savait pas. La mesure avait été faite dans l'instrument qui se trompait.
--
-- ─────────────────────────────────────────────────────────────────────
-- AUCUNE PHOTO N'EST PRISE ICI, ET C'EST VOULU.
--
-- Passer par la route HTTP aurait déclenché une photo par projet corrigé,
-- avec les chiffres du budget D'AUJOURD'HUI, étiquetée comme la photo du
-- moment de la victoire. Pour le 290, que son chargé de projet modifie tous
-- les jours, ce serait un faux : l'état du 1er octobre présenté comme celui
-- du jour où l'appel d'offres a été remporté.
--
-- On rétablit la VÉRITÉ (le statut) sans INVENTER d'histoire (la photo).
-- Simon décide séparément s'il veut des photos, en sachant qu'elles
-- porteraient les chiffres du jour où on les prend.
-- ─────────────────────────────────────────────────────────────────────
--
-- LES VALEURS SONT ÉCRITES EN DUR, ET C'EST DÉLIBÉRÉ. `ad_budget` et
-- `app_hub` sont deux bases distinctes : ce fichier ne peut pas lire le hub.
-- Les 17 couples ci-dessous ont été relevés dans le hub le 1er oct à 20 h 07.
--
-- CHAQUE LIGNE PORTE SA CONDITION SUR LA VALEUR DE DÉPART. Si un statut a
-- changé depuis le relevé — par la main de quelqu'un, ou par le nouveau lien
-- descendant — la ligne ne correspond plus et on n'écrit PAS. On ne piétine
-- jamais une décision plus récente que la mesure. C'est aussi ce qui rend ce
-- fichier idempotent : rejoué, il ne trouve plus rien.
-- ════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    n integer;
    total integer := 0;
BEGIN
    -- ── 14 projets que le hub sait GAGNÉS ────────────────────────────
    -- 290 est « Rénovation intérieure d'unités de logements », le 3 M$.
    -- 326 et 327 sont les deux « Oslo Traiteur (copie) » : Simon les a
    -- confirmés comme de vrais projets (20:10).
    UPDATE ad_budget.projets SET statut = 'en_cours'
     WHERE statut = 'en_soumission'
       AND supprime_le IS NULL
       AND id IN (149, 152, 192, 193, 218, 274, 276, 279, 287, 290, 320, 323, 326, 327);
    GET DIAGNOSTICS n = ROW_COUNT; total := total + n;
    RAISE NOTICE 'alignes vers en_cours : % (14 attendus)', n;

    -- ── 2 projets que le hub sait PERDUS, depuis « en soumission » ───
    -- 286 « Installation de nouvelles bornes d'urgences extérieures »
    -- 309 « ACQ-Bureau 2e étages »
    UPDATE ad_budget.projets SET statut = 'perdu'
     WHERE statut = 'en_soumission'
       AND supprime_le IS NULL
       AND id IN (286, 309);
    GET DIAGNOSTICS n = ROW_COUNT; total := total + n;
    RAISE NOTICE 'alignes vers perdu depuis en_soumission : % (2 attendus)', n;

    -- ── 1 projet que le hub sait PERDU, depuis « en cours » ──────────
    -- 324 « Construction de huttes de survie » : projet TEST, perdu au hub
    -- (Simon, 20:07), mais qu'Ad BUD croyait EN COURS — d'où son unique
    -- photo du 21 septembre, qui annonce une victoire qui n'a pas eu lieu.
    UPDATE ad_budget.projets SET statut = 'perdu'
     WHERE statut = 'en_cours'
       AND supprime_le IS NULL
       AND id = 324;
    GET DIAGNOSTICS n = ROW_COUNT; total := total + n;
    RAISE NOTICE 'aligne vers perdu depuis en_cours : % (1 attendu)', n;

    RAISE NOTICE 'TOTAL aligne : % (17 attendus)', total;
    IF total <> 17 THEN
        RAISE NOTICE 'ECART : % lignes au lieu de 17. Un statut a bouge depuis '
                     'le releve du 1er oct 20h07. Les lignes non touchees portent '
                     'une valeur PLUS RECENTE que la mesure : c''est voulu, on ne '
                     'les ecrase pas. A reverifier contre le hub.', total;
    END IF;
END $$;

-- ════════════════════════════════════════════════════════════════════
-- CE QU'ON NE TOUCHE PAS
--
-- Les 6 projets déjà d'accord avec le hub : 154 (Presse de compaction),
-- 277 (test) et 278 (test2) — que Simon a qualifiés de « tests pas bons »
-- et qui concordaient déjà —, 321 (Aménagement Santé Québec),
-- 322 (Alimentation d'urgence ABP), 325 (dalle de béton, patinoire).
--
-- Et AUCUN budget, AUCUNE ligne, AUCUNE photo. Seul le statut bouge.
-- ════════════════════════════════════════════════════════════════════
