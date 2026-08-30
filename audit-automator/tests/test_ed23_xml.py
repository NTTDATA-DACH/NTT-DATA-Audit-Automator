"""Tests for the official BSI XML parser (src/tools/ed23_xml.py).

Stdlib + pytest only, no network: the DocBook fragment below is self-contained. Ported
from the Grundschutz-Plus-Plus-Tools test of the same parser, extended with the
Baustein-title harvesting this repo adds.
"""

from src.tools import ed23_xml as mod
from src.tools.sentence_split import split_sentences


def test_split_sentences_semantics():
    assert split_sentences("") == []
    assert split_sentences(None) == []
    text = "Es MUSS geprüft werden, z. B. jährlich. Zudem SOLLTE dokumentiert werden."
    assert split_sentences(text) == [
        "Es MUSS geprüft werden, z. B. jährlich.",
        "Zudem SOLLTE dokumentiert werden.",
    ]
    assert split_sentences("Eins  bzw.\n zwei. Drei!") == ["Eins bzw. zwei.", "Drei!"]


def test_title_regex_variants():
    cases = {
        "ISMS.1.A1 Übernahme der Gesamtverantwortung (B) [Institutionsleitung]":
            ("ISMS.1.A1", "Übernahme der Gesamtverantwortung", "B", "Institutionsleitung"),
        "APP.4.4.A21 Regelmäßiger Restart von Pods (H)":
            ("APP.4.4.A21", "Regelmäßiger Restart von Pods", "H", None),
        "INF.14.A1 Planung der Gebäudeautomation (B)":
            ("INF.14.A1", "Planung der Gebäudeautomation", "B", None),
        "SYS.1.2.2.A27 Titel mit Nummer A27 (S)":
            ("SYS.1.2.2.A27", "Titel mit Nummer A27", "S", None),
        # Defensive: reversed order role-before-level still recovers both
        "NET.1.1.A9 Dokumentation [IT-Betrieb] (B)":
            ("NET.1.1.A9", "Dokumentation", "B", "IT-Betrieb"),
        "OPS.1.1.2.A2 ENTFALLEN (B)": ("OPS.1.1.2.A2", "ENTFALLEN", "B", None),
    }
    for title, expected in cases.items():
        assert mod.parse_requirement_title(title) == expected, title
    assert mod.parse_requirement_title("Gefährdungslage") is None
    assert mod.parse_requirement_title("2.1 Fehlende Regelung") is None


DOCBOOK_FRAGMENT = """<?xml version="1.0" encoding="UTF-8"?>
<book xmlns="http://docbook.org/ns/docbook">
  <chapter><title>TST.1 Testbaustein</title>
    <section><title>2. Gefährdungslage</title>
      <section><title>TST.9.A9 Sieht aus wie eine Anforderung (B)</title>
        <para>Darf nicht mitzählen, falscher Ahnen-Kontext.</para>
      </section>
    </section>
    <section><title>3. Anforderungen</title>
      <section><title>3.1. Basis-Anforderungen</title>
        <section><title>TST.1.A1 Erste Pflicht (B) [Rolle X]</title>
          <para>Die Institution MUSS planen. Sie MUSS z.&#160;B. dokumentieren.</para>
          <para>Hintergrundsatz ohne Modalverb.</para>
        </section>
        <section><title>TST.1.A2 ENTFALLEN (B)</title>
          <para>Diese Anforderung ist entfallen.</para>
        </section>
      </section>
      <section><title>3.2. Standard-Anforderungen</title>
        <section><title>TST.1.A3 Zweite Pflicht (S)</title>
          <para>Es SOLLTEN <emphasis>alle</emphasis> Systeme erfasst werden. Dazu gehören:</para>
          <itemizedlist>
            <listitem><para>Server.</para></listitem>
            <listitem><para>Clients.</para></listitem>
          </itemizedlist>
        </section>
      </section>
    </section>
  </chapter>
</book>
"""


def test_docbook_fragment_parse():
    reqs, rejected = mod.load_official_xml(DOCBOOK_FRAGMENT.encode("utf-8"))
    assert set(reqs) == {"TST.1.A1", "TST.1.A2", "TST.1.A3"}
    # The requirement-looking section under Gefährdungslage is rejected, not silently dropped
    assert any("TST.9.A9" in t for t in rejected)

    a1 = reqs["TST.1.A1"]
    assert (a1["level"], a1["sublevel"], a1["rolle"]) == ("B", "B", "Rolle X")
    assert a1["entfallen"] is False
    assert a1["baustein"] == "TST.1"
    assert a1["schicht"] == "TST"
    # &#160; is normalized so the abbreviation mask holds: 3 sentences, 2 normative
    assert len(a1["saetze"]) == 3
    assert a1["normative_idx"] == [1, 2]
    assert a1["prose"].startswith("Die Institution MUSS planen.")

    assert reqs["TST.1.A2"]["entfallen"] is True

    a3 = reqs["TST.1.A3"]
    assert a3["has_lists"] is True
    assert a3["level"] == "S"
    assert 1 in a3["normative_idx"]


def test_normative_detection():
    # lowercase "muss"/"kann" never counts as normative
    assert not mod.NORMATIVE_RE.search("Das muss man wissen.")
    assert mod.NORMATIVE_RE.search("Der Zugriff DARF NICHT erfolgen.")
    assert mod.NORMATIVE_RE.search("Alle MÜSSEN teilnehmen.")
    assert not mod.NORMATIVE_RE.search("Die Institution KANN erweitern.")
    assert mod.KANN_RE.search("Die Institution KANN erweitern.")


def test_baustein_titles_are_harvested_and_filtered():
    titles = mod.load_baustein_titles(DOCBOOK_FRAGMENT.encode("utf-8"), keep_ids={"TST.1"})
    assert titles == {"TST.1": "Testbaustein"}
    # Without the filter, numbered headings that are not Bausteine come along — which is
    # exactly why the converter passes the set of Bausteine that own a requirement.
    unfiltered = mod.load_baustein_titles(DOCBOOK_FRAGMENT.encode("utf-8"))
    assert unfiltered["TST.1"] == "Testbaustein"
    # A requirement title must never be mistaken for a Baustein heading.
    assert not any(".A" in bid for bid in unfiltered)
