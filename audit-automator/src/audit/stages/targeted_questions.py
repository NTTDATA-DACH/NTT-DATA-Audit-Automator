# file: src/audit/stages/targeted_questions.py
"""Shared handling of the question blocks that chapters 3 and 5 answer from the A.4 data.

A question block is a list of Ja/Nein answers plus the Feststellungen that go with it. Some
answers are computed from the extracted Grundschutz-Check, some are a targeted model call over
one reference document; both end up in the same shape, so the two stages share the code that
records a model answer and the code that packs the findings for the report.
"""
import logging
from typing import Any, Dict, List


def record_targeted_answer(
    res: Dict[str, Any], answers: List, idx: int, findings: List, question_label: str
) -> None:
    """Safely apply a single-question AI response to `answers`/`findings`.

    The AI client validates every reply against the schema it sent, so a response missing
    `answers`/`finding` is retried there first. This stays as the last line of defence and
    fixes the policy for a laxer schema: rather than letting an IndexError/KeyError abort the
    whole stage, a malformed reply is scored conservatively (answer = False) and flagged as an
    'AG' finding so it surfaces for human review rather than silently passing.
    """
    answer_list = res.get("answers") if isinstance(res, dict) else None
    if isinstance(answer_list, list) and answer_list:
        answers[idx] = answer_list[0]
    else:
        answers[idx] = False
        logging.warning(f"[{question_label}] Malformed AI response: missing/empty 'answers'. Scoring as not fulfilled.")
        findings.append({"category": "AG", "description": f"Die KI-Antwort für '{question_label}' war unvollständig (kein 'answers'-Feld) und wurde als nicht erfüllt gewertet."})

    finding = res.get("finding") if isinstance(res, dict) else None
    if isinstance(finding, dict):
        if finding.get("category", "OK") != "OK":
            findings.append(finding)
    else:
        logging.warning(f"[{question_label}] Malformed AI response: missing/invalid 'finding'.")
        findings.append({"category": "AG", "description": f"Die KI-Antwort für '{question_label}' enthielt kein gültiges 'finding'-Objekt."})


def finding_payload(findings: List[Dict[str, Any]], ok_description: str) -> Dict[str, Any]:
    """One Feststellung per deficiency, or the single OK finding when there is none.

    The sub-checks used to be joined into a single 'Zusammenfassung: a | b | c' text, so
    chapter 7.2 carried one row mixing unrelated deficiencies and the final stage could only
    merge or close that row as a whole.
    """
    if not findings:
        return {"finding": {"category": "OK", "description": ok_description}}
    return {"findings": findings}


def list_requirement_ids(labels: List[str], limit: int) -> str:
    """'A, B, C und 7 weitere': the IDs a finding names, capped so a row stays readable."""
    shown = ", ".join(labels[:limit])
    if len(labels) > limit:
        shown += f" und {len(labels) - limit} weitere"
    return shown
