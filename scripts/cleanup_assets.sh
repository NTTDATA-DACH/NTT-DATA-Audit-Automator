#!/bin/bash
#
# This script cleans up the redundant prompt and schema files after
# refactoring Chapter3Runner to use generic, template-driven assets.
# Run this from the project root.
#
set -euo pipefail

echo "🗑️  This script will delete 28 redundant asset files."
read -p "Are you sure you want to proceed? (y/n) " -n 1 -r
echo

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted by user."
    exit 1
fi

echo "--- Deleting Redundant Prompts ---"
rm -v assets/prompts/stage_3_3_10_ergebnis_struktur.txt || true
rm -v assets/prompts/stage_3_3_4_anwendungen.txt || true
rm -v assets/prompts/stage_3_3_5_itsysteme.txt || true
rm -v assets/prompts/stage_3_3_6_raeume.txt || true
rm -v assets/prompts/stage_3_3_7_kommunikation.txt || true
rm -v assets/prompts/stage_3_3_9_dienstleister.txt || true
rm -v assets/prompts/stage_3_4_2_schutzbedarf_gp.txt || true
rm -v assets/prompts/stage_3_4_3_schutzbedarf_anw.txt || true
rm -v assets/prompts/stage_3_4_4_schutzbedarf_its.txt || true
rm -v assets/prompts/stage_3_4_5_schutzbedarf_raeume.txt || true
rm -v assets/prompts/stage_3_4_6_schutzbedarf_komm.txt || true
rm -v assets/prompts/stage_3_4_8_ergebnis_schutzbedarf.txt || true
rm -v assets/prompts/stage_3_6_2_bausteine_custom.txt || true
rm -v assets/prompts/stage_3_6_3_ergebnis_check.txt || true
rm -v assets/prompts/stage_3_7_1_ergebnis_risikoanalyse.txt || true
rm -v assets/prompts/stage_3_8_1_ergebnis_realisierungsplan.txt || true

echo "--- Deleting Redundant Schemas ---"
rm -v assets/schemas/stage_3_3_10_ergebnis_struktur_schema.json || true
rm -v assets/schemas/stage_3_3_4_anwendungen_schema.json || true
rm -v assets/schemas/stage_3_3_5_itsysteme_schema.json || true
rm -v assets/schemas/stage_3_3_6_raeume_schema.json || true
rm -v assets/schemas/stage_3_3_7_kommunikation_schema.json || true
rm -v assets/schemas/stage_3_3_9_dienstleister_schema.json || true
rm -v assets/schemas/stage_3_4_2_schutzbedarf_gp_schema.json || true
rm -v assets/schemas/stage_3_4_3_schutzbedarf_anw_schema.json || true
rm -v assets/schemas/stage_3_4_4_schutzbedarf_its_schema.json || true
rm -v assets/schemas/stage_3_4_5_schutzbedarf_raeume_schema.json || true
rm -v assets/schemas/stage_3_4_6_schutzbedarf_komm_schema.json || true
rm -v assets/schemas/stage_3_4_8_ergebnis_schutzbedarf_schema.json || true
rm -v assets/schemas/stage_3_6_2_bausteine_custom_schema.json || true
rm -v assets/schemas/stage_3_6_3_ergebnis_check_schema.json || true
rm -v assets/schemas/stage_3_7_1_ergebnis_risikoanalyse_schema.json || true
rm -v assets/schemas/stage_3_8_1_ergebnis_realisierungsplan_schema.json || true

echo "✅ Cleanup complete."