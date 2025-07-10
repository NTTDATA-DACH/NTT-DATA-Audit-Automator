#!/bin/bash
# Deletes the now-obsolete individual prompt text files.

echo "Deleting old prompt files from ./assets/prompts/ ..."

rm -f ./assets/prompts/etl_classify_documents.txt
rm -f ./assets/prompts/generic_question_prompt.txt
rm -f ./assets/prompts/generic_summary_prompt.txt
rm -f ./assets/prompts/stage_1_2_geltungsbereich.txt
rm -f ./assets/prompts/stage_1_4_informationsverbund.txt
rm -f ./assets/prompts/stage_3_1_aktualitaet.txt
rm -f ./assets/prompts/stage_3_2_sicherheitsleitlinie.txt
rm -f ./assets/prompts/stage_3_3_1_informationsverbund.txt
rm -f ./assets/prompts/stage_3_3_2_netzplan.txt
rm -f ./assets/prompts/stage_3_3_3_geschaeftsprozesse.txt
rm -f ./assets/prompts/stage_3_4_1_schutzbedarfskategorien.txt
rm -f ./assets/prompts/stage_3_5_1_modellierungsdetails.txt
rm -f ./assets/prompts/stage_3_5_2_ergebnis_modellierung.txt
rm -f ./assets/prompts/stage_3_6_1_extract_check_data.txt
rm -f ./assets/prompts/stage_3_6_1_grundschutz_check.txt
rm -f ./assets/prompts/stage_3_9_ergebnis.txt
rm -f ./assets/prompts/stage_4_1_1_auswahl_bausteine_erst.txt
rm -f ./assets/prompts/stage_4_1_2_auswahl_bausteine_ueberwachung.txt
rm -f ./assets/prompts/stage_4_1_5_auswahl_massnahmen_risiko.txt
rm -f ./assets/prompts/stage_7_2_abweichungen.txt

# Also remove the now-empty directory
rmdir ./assets/prompts 2>/dev/null || true

echo "Deletion complete."