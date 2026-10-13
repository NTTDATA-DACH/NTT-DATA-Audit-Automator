"""Chapter 3 of the report template must match the BSI Muster-Auditbericht V3.0 (Kompendium).

The Muster is not public (request it from gs-zert@bsi.bund.de); the expectations below were
taken from its chapter 3 on 2026-10-13: question count, Feststellung, Votum label and table
headers of every section. Each question-bearing section must also have a Chapter-3 prompt
config whose schema demands exactly that many boolean answers — a template question the
model never answers (or an answer with no question) is mapped positionally and silently
corrupts the report. Issues #11–#14 were exactly such drifts.
"""
import json

TEMPLATE_PATH = "assets/json/master_report_template.json"
PROMPT_CONFIG_PATH = "assets/json/prompt_config.json"

# (path under dokumentenpruefung, number, #questions, has Feststellung, Votum label, table headers)
EXPECTED = [
    ("aktualitaetDerReferenzdokumente", "3.1", 4, True, None, None),
    ("sicherheitsleitlinieUndRichtlinienInA0", "3.2", 3, True, None, None),
    ("strukturanalyseA1.definitionDesInformationsverbundes", "3.3.1", 3, True, None, None),
    ("strukturanalyseA1.bereinigterNetzplan", "3.3.2", 2, True, None, None),
    ("strukturanalyseA1.listeDerGeschaeftsprozesse", "3.3.3", 1, True, None, None),
    ("strukturanalyseA1.listeDerAnwendungen", "3.3.4", 1, True, None, None),
    ("strukturanalyseA1.listeDerItSysteme", "3.3.5", 1, True, None, None),
    ("strukturanalyseA1.listeDerRaeumeGebaeudeStandorte", "3.3.6", 1, True, None, None),
    ("strukturanalyseA1.listeDerKommunikationsverbindungen", "3.3.7", 1, True, None, None),
    ("strukturanalyseA1.stichprobenDokuStrukturanalyse", "3.3.8", 1, True, None,
     ["Nr.", "Bezeichnung", "Beschreibung", "Anzahl", "Enthält alle Informationen", "Enthält alle Zuordnungen"]),
    ("strukturanalyseA1.listeDerDienstleister", "3.3.9", 1, True, None, None),
    ("strukturanalyseA1.ergebnisDerStrukturanalyse", "3.3.10", 0, False, "Votum Strukturanalyse:", None),
    ("schutzbedarfsfeststellungA2.definitionDerSchutzbedarfskategorien", "3.4.1", 2, True, None, None),
    ("schutzbedarfsfeststellungA2.schutzbedarfGeschaeftsprozesse", "3.4.2", 2, True, None, None),
    ("schutzbedarfsfeststellungA2.schutzbedarfAnwendungen", "3.4.3", 2, True, None, None),
    ("schutzbedarfsfeststellungA2.schutzbedarfItSysteme", "3.4.4", 2, True, None, None),
    ("schutzbedarfsfeststellungA2.schutzbedarfRaeume", "3.4.5", 2, True, None, None),
    ("schutzbedarfsfeststellungA2.schutzbedarfKommunikationsverbindungen", "3.4.6", 3, True, None, None),
    ("schutzbedarfsfeststellungA2.stichprobenDokuSchutzbedarf", "3.4.7", 0, False, None,
     ["Nr.", "Bezeichnung", "Beschreibung", "Ableitung des Schutzbedarfs nachvollziehbar", "Verknüpfungen zu Zielobjekten nachvollziehbar"]),
    ("schutzbedarfsfeststellungA2.ergebnisDerSchutzbedarfsfeststellung", "3.4.8", 0, False, "Votum Schutzbedarfsfeststellung:", None),
    ("modellierungDesInformationsverbundesA3", "3.5", 1, True, None, None),
    ("modellierungDesInformationsverbundesA3.modellierungsdetails", "3.5.1", 4, True, None, None),
    ("modellierungDesInformationsverbundesA3.ergebnisDerModellierung", "3.5.2", 0, False, "Votum Modellierung:", None),
    ("itGrundschutzCheckA4.detailsZumItGrundschutzCheck", "3.6.1", 5, True, None, None),
    ("itGrundschutzCheckA4.benutzerdefinierteBausteine", "3.6.2", 2, True, None, None),
    ("itGrundschutzCheckA4.ergebnisItGrundschutzCheck", "3.6.3", 0, False, "Votum IT-Grundschutz-Check:", None),
    ("risikoanalyseA5", "3.7", 6, True, None, None),
    ("risikoanalyseA5.risikoanalyse", "3.7.1", 0, False, "Votum Risikoanalyse:", None),
    ("realisierungsplanA6", "3.8", 3, True, None, None),
    ("realisierungsplanA6.realisierungsplan", "3.8.1", 0, False, "Votum Realisierungsplan:", None),
    ("ergebnisDerDokumentenpruefung", "3.9", 1, False, "Votum Dokumentenprüfung:", None),
]


def _chapter_3():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        return json.load(f)["bsiAuditReport"]["dokumentenpruefung"]


def _section(path):
    node = _chapter_3()
    for part in path.split("."):
        node = node[part]
    return node


def _prompt_config():
    with open(PROMPT_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)["stages"]["Chapter-3"]


def test_every_section_matches_the_muster():
    for path, number, n_questions, has_finding, votum_label, headers in EXPECTED:
        section = _section(path)
        content = section.get("content", [])
        assert section["subchapterNumber"] == number, path
        questions = [i for i in content if i.get("type") == "question"]
        assert len(questions) == n_questions, f"{number}: {len(questions)} questions, Muster has {n_questions}"
        findings = [i for i in content if i.get("type") == "finding"]
        assert bool(findings) == has_finding, f"{number}: Feststellung present={bool(findings)}, Muster: {has_finding}"
        vota = [i.get("label") for i in content if i.get("type") == "prose"]
        assert vota == ([votum_label] if votum_label else []), f"{number}: Votum {vota}"
        if headers:
            assert section["table"]["headers"] == headers, f"{number}: table headers"
            assert section["table"]["rows"] == [], f"{number}: template rows must start empty"
        else:
            assert "table" not in section, f"{number}: Muster has no table here"


def test_no_unlisted_sections_in_chapter_3():
    listed = {p for p, *_ in EXPECTED}
    found = set()
    for key, data in _chapter_3().items():
        if not isinstance(data, dict):
            continue
        if "content" in data:
            found.add(key)
        for sub, sub_data in data.items():
            if isinstance(sub_data, dict):
                found.add(f"{key}.{sub}")
    assert found == listed, f"unexpected: {found - listed}, missing: {listed - found}"


def _schema(cfg):
    with open(cfg["schema_path"], encoding="utf-8") as f:
        return json.load(f)


def test_schema_answers_match_template_questions():
    config = _prompt_config()
    for path, number, n_questions, _, votum_label, headers in EXPECTED:
        key = path.split(".")[-1]
        cfg = config.get(key)
        if votum_label:
            assert cfg and cfg.get("type") == "summary", f"{number}: a Votum section must be a summary task"
            continue
        assert cfg, f"{number} ({key}) has no Chapter-3 prompt config"
        if cfg.get("type") == "custom_logic":
            continue
        schema = _schema(cfg)
        answers = schema["properties"].get("answers")
        if n_questions:
            assert answers, f"{number}: schema has no 'answers'"
            assert answers["minItems"] == answers["maxItems"] == n_questions, (
                f"{number}: schema demands {answers.get('minItems')}-{answers.get('maxItems')} answers, template has {n_questions} questions")
            # 3.1's last answer is the date of the last A.4 change, hence the anyOf there.
            accepted = answers["items"] == {"type": "boolean"} or {"type": "boolean"} in answers["items"].get("anyOf", [])
            assert accepted, f"{number}: answers must be booleans (the editor renders Ja/Nein/N/A)"
        else:
            assert not answers, f"{number}: schema has answers but the template has no questions"
        if headers:
            assert "rows" in schema["properties"]["table"]["properties"], f"{number}: table schema must define rows"


def test_dependent_and_optional_sections_are_wired():
    config = _prompt_config()
    assert "{stichproben_json}" in config["stichprobenDokuSchutzbedarf"]["prompt"], "3.4.7 must receive the 3.3.8 samples"
    assert "{questions}" not in config["stichprobenDokuStrukturanalyse"]["prompt"], "3.3.8's Netzplan question is appended by the generic block"
