-- ════════════════════════════════════════════════════════════════════
-- Sprint Gabarits — Gabarit PAR DÉFAUT par utilisateur
--
-- Les gabarits restent PRIVÉS PAR ORGANISATION (bibliothèque partagée entre
-- les users d'une même org). Le choix du gabarit par défaut est PERSONNEL :
-- chaque user a AU PLUS un gabarit par défaut (user_id = PK → un seul défaut
-- par user). Deux users d'une même org partagent les gabarits mais peuvent
-- avoir des défauts différents.
--
-- gabarit_id FK CASCADE : si le gabarit défaut est supprimé, la ligne défaut
-- disparaît (le user n'a plus de défaut). Idempotente. Aucune vue dépendante
-- (table nouvelle).
-- ════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS ad_budget.user_gabarit_defaut (
  user_id         INTEGER PRIMARY KEY,
  gabarit_id      INTEGER NOT NULL
                    REFERENCES ad_budget.gabarits(id) ON DELETE CASCADE,
  organization_id UUID NOT NULL,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_gabarit_defaut_org
  ON ad_budget.user_gabarit_defaut (organization_id);
