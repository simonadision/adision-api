    # ════════════════════════════════════════════════════════════════════
    # Ad VIU v2 - Push d'une analyse vers Ad BUD (Jalon 4)
    # ════════════════════════════════════════════════════════════════════
    @router.post("/projects/from-viu-v2")
    def projects_from_viu_v2(data: dict, user=Depends(jwt_user)):
        """Cree un projet (mode=new) ou ajoute des lignes a un projet existant
        (mode=existing) a partir des items detectes/valides par Ad VIU v2.

        Mapping items v2 -> budget_lignes :
        - csi_section -> section
        - description -> description
        - unite -> unite (defaut 'global')
        - quantite (deja calculee cote frontend : finale > ia > 0) -> qte
        - prix_unitaire = 0 (l'utilisateur ajustera dans Ad BUD)
        - source_viu_analysis_id, source_viu_item_id : tracabilite + idempotence

        Idempotence : si une ligne avec le meme source_viu_item_id existe deja
        dans le projet, on SKIP (pas d'UPDATE, pour preserver les modifs manuelles
        que l'user aurait faites dans Ad BUD entre les deux pushes).
        """
        mode = (data.get("mode") or "").strip()
        if mode not in ("new", "existing"):
            raise HTTPException(status_code=400, detail="mode doit etre 'new' ou 'existing'")

        items = data.get("items") or []
        if not isinstance(items, list) or not items:
            raise HTTPException(status_code=400, detail="Aucun item a pousser")

        source_analysis_id = data.get("source_analysis_id")
        try:
            source_analysis_id = int(source_analysis_id) if source_analysis_id is not None else None
        except (TypeError, ValueError):
            source_analysis_id = None

        source_file = (data.get("source_pdf_filename") or "").strip() or None

        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            template_lines_added = 0
            if mode == "new":
                project_name = (data.get("project_name") or "").strip()
                if not project_name:
                    raise HTTPException(
                        status_code=400,
                        detail="project_name requis pour mode=new",
                    )
                cur.execute(
                    """
                    INSERT INTO ad_budget.projets (user_id, nom, statut)
                    VALUES (%s, %s, 'brouillon')
                    RETURNING id, nom
                    """,
                    (user["id"], project_name),
                )
                proj = cur.fetchone()
                template_lines_added = _apply_master_template(cur, proj["id"])
            else:
                project_id_in = data.get("project_id")
                if not project_id_in:
                    raise HTTPException(
                        status_code=400,
                        detail="project_id requis pour mode=existing",
                    )
                cur.execute(
                    "SELECT id, nom, user_id FROM ad_budget.projets WHERE id = %s",
                    (project_id_in,),
                )
                proj = cur.fetchone()
                if not proj:
                    raise HTTPException(status_code=404, detail="Projet introuvable")
                if proj["user_id"] != user["id"]:
                    raise HTTPException(
                        status_code=403,
                        detail="Ce projet ne vous appartient pas",
                    )

            project_id = proj["id"]

            cur.execute(
                """
                SELECT DISTINCT section
                FROM ad_budget.budget_lignes
                WHERE projet_id = %s AND section IS NOT NULL AND section <> ''
                """,
                (project_id,),
            )
            existing_sections = {row["section"] for row in cur.fetchall()}

            sections_touched = set()
            lines_added = 0
            lines_skipped = 0

            note_prefix = (
                f"Importe d'Ad VIU v2 - Analyse #{source_analysis_id}"
                if source_analysis_id else "Importe d'Ad VIU v2"
            )

            for item in items:
                section = (item.get("csi_section") or "").strip()
                description = (item.get("description") or "").strip()
                if not description:
                    continue

                unite = (item.get("unite") or "global").strip() or "global"

                qte = item.get("quantite")
                if qte is None:
                    qte = item.get("quantite_ia")
                try:
                    qte = float(qte) if qte is not None else 0.0
                except (TypeError, ValueError):
                    qte = 0.0

                viu_item_id = item.get("id")
                try:
                    viu_item_id = int(viu_item_id) if viu_item_id is not None else None
                except (TypeError, ValueError):
                    viu_item_id = None

                # Idempotence : skip si deja pousse
                if viu_item_id is not None:
                    cur.execute(
                        """
                        SELECT id FROM ad_budget.budget_lignes
                        WHERE projet_id = %s AND source_viu_item_id = %s
                        LIMIT 1
                        """,
                        (project_id, viu_item_id),
                    )
                    if cur.fetchone():
                        lines_skipped += 1
                        continue

                note_parts = [note_prefix]
                item_notes = (item.get("notes") or "").strip()
                if item_notes:
                    note_parts.append(item_notes)
                note_text = " | ".join(note_parts)

                cur.execute(
                    """
                    INSERT INTO ad_budget.budget_lignes
                      (projet_id, section, description, unite, prix_unitaire,
                       qte, ajustement_pct, note, actif, source_file, type_source,
                       source_viu_analysis_id, source_viu_item_id)
                    VALUES (%s, %s, %s, %s, 0, %s, 0, %s, TRUE, %s, 'viu_v2',
                            %s, %s)
                    """,
                    (
                        project_id, section or None, description, unite,
                        qte, note_text,
                        source_file,
                        source_analysis_id, viu_item_id,
                    ),
                )
                lines_added += 1
                if section:
                    sections_touched.add(section)

            new_sections_count = len(sections_touched - existing_sections)
            conn.commit()
            return {
                "project_id": project_id,
                "project_name": proj["nom"],
                "sections_added": new_sections_count,
                "lines_added": lines_added,
                "lines_skipped": lines_skipped,
                "template_lines_added": template_lines_added,
            }
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Erreur push : {e}")
        finally:
            cur.close()
            conn.close()
