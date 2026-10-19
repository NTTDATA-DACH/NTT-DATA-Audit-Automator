# src/audit/report_finalizer.py
"""Final stage over the assembled (and hand-edited) report: one finding per Sachverhalt,
final numbers, and those numbers in the chapter texts.

Runs after `--generate-report` and after the auditor has edited the report, so it works on
the report JSON itself, never on the stage results: the hand edits are the single source of
truth by then. Everything outside the findings is copied through untouched, which includes
the audit plan in chapter 4 the audit has already been scheduled by.

Deterministic first, the model on the residual:
  * rows with identical text (after normalising) are duplicates by code;
  * the model groups the semantic duplicates in ONE call over all rows of chapter 7.2 and
    names the most comprehensive finding of each group; the checker re-judges the groups;
  * code applies a group only at or above FINALIZER_CONFIDENCE_THRESHOLD and only with IDs
    it sent, closes a superseded finding of the previous audit with a reference to the
    newer one, drops a new finding contained in an existing one, renumbers the remaining
    new findings per category after the previous audit's numbers, and writes the final
    number into every "Feststellung:" item of the chapters;
  * the same model call names the open findings of the previous audit that concern the
    document review (A.0 to A.6); those the current audit did not find again are closed,
    since the documents have been reviewed in full.
"""
import difflib
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from google.cloud.exceptions import NotFound

from src.assets_loader import load_asset_json
from src.audit.report_generator import ReportGenerator
from src.clients.ai_client import AiClient, current_stage
from src.clients.gcs_client import GcsClient
from src.config import AppConfig
from src.constants import (
    FINAL_REPORT_SUFFIX,
    FINALIZER_BASE,
    FINALIZER_CONFIDENCE_THRESHOLD,
    PROMPT_CONFIG_PATH,
    REPORT_FILE_PREFIX,
    RESULTS_BASE,
)

STAGE_NAME = "Finalize-Report"

# Chapter 7.2: one table per category. The table a row sits in decides its category.
FINDINGS_TABLES = {
    "AS": "schwerwiegendeAbweichungen",
    "AG": "geringfuegigeAbweichungen",
    "E": "empfehlungen",
}
DESCRIPTION_COLUMNS = ("Beschreibung der Abweichung", "Beschreibung der Empfehlung")
PREVIOUS_SOURCE_PREFIX = "Previous Audit"
# A previous-audit finding whose status starts with one of these is already closed; every
# other status ("Offen", "Teilweise", "Zur Umsetzung empfohlen", ...) counts as open.
CLOSED_STATUS_PREFIXES = ("korrekt", "geschlossen", "behoben", "erledigt", "umgesetzt")
NOT_REFOUND_STATUS = "Geschlossen (Dokumentenprüfung: nicht erneut festgestellt)"

_ID_RE = re.compile(r"^\s*(AG|AS|E)-0*(\d+)\s*$")
# "[AG] text", "[AG-12] text" and the untouched "[OK] text" of a Feststellung item.
_FINDING_TEXT_RE = re.compile(r"^\[(AG|AS|E|OK)(?:-\d+)?\]\s*(.*)$", re.DOTALL)
# Old reports repeat the number inside the text ("AG-8.: ..."); it is no part of the content.
_LEADING_ID_RE = re.compile(r"^(ag|as|e)-\d+\s*\.?\s*:?\s*")
_NON_WORD_RE = re.compile(r"[^0-9a-zäöüß]+")
# Near-identical texts (a hand edit of one copy) still count as the same finding.
_CHAPTER_MATCH_RATIO = 0.9


def normalize_text(text: Any) -> str:
    """Comparison form of a finding text: case, punctuation, spacing and a leading ID ignored."""
    lowered = str(text or "").strip().lower()
    lowered = _LEADING_ID_RE.sub("", lowered)
    return " ".join(_NON_WORD_RE.sub(" ", lowered).split())


def is_open_status(status: Any) -> bool:
    """True unless the status says the finding is done."""
    return not str(status or "").strip().lower().startswith(CLOSED_STATUS_PREFIXES)


def parse_finding_id(value: Any) -> Tuple[Optional[str], Optional[int]]:
    """'AG-08' -> ('AG', 8); anything else -> (None, None)."""
    match = _ID_RE.match(str(value or ""))
    if not match:
        return None, None
    return match.group(1), int(match.group(2))


@dataclass
class Finding:
    """One row of chapter 7.2 while the stage works on it."""
    key: str                      # the model's handle: the row's Nummer, made unique
    category: str                 # from the table the row sits in
    number: Optional[int]
    description: str
    source: str
    previous: bool
    open: bool                    # status does not say the finding is done
    row: Dict[str, Any]
    order: int                    # position over all tables, for stable sorting
    final_id: Optional[str] = None
    folded_into: Optional[str] = None   # key of the finding this one is dropped in favour of
    closed_by: Optional[str] = None     # key of the newer finding this previous one yields to
    refound: bool = False               # a finding of the current audit was folded into this one
    closed_not_refound: bool = False    # document-review finding the current audit did not meet again

    @property
    def dropped(self) -> bool:
        return self.folded_into is not None


@dataclass
class Decision:
    """One duplicate group and what became of it."""
    ids: List[str]
    behalten: str
    konfidenz: float
    begruendung: str
    quelle: str                   # "deterministisch" | "modell"
    status: str = "offen"
    wirkung: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ids": self.ids, "behalten": self.behalten, "konfidenz": self.konfidenz,
            "begruendung": self.begruendung, "quelle": self.quelle, "status": self.status,
            "wirkung": self.wirkung,
        }


def collect_findings(report: Dict[str, Any]) -> List[Finding]:
    """Reads the three tables of chapter 7.2 into Finding objects (rows are shared, not copied)."""
    tables = report["bsiAuditReport"]["anhang"]["abweichungenUndEmpfehlungen"]
    findings: List[Finding] = []
    seen_keys: Dict[str, int] = {}
    for category, table_key in FINDINGS_TABLES.items():
        rows = tables.get(table_key, {}).get("table", {}).get("rows", [])
        for row in rows:
            nummer = str(row.get("Nummer") or "").strip()
            id_category, number = parse_finding_id(nummer)
            if id_category and id_category != category:
                logging.warning(
                    f"Row '{nummer}' sits in the {category} table but carries a {id_category} number; "
                    f"the table decides, the row is treated as {category}."
                )
            key = nummer or f"{category}-?"
            seen_keys[key] = seen_keys.get(key, 0) + 1
            if seen_keys[key] > 1:
                key = f"{key}#{seen_keys[key]}"
            description = next((str(row[c]) for c in DESCRIPTION_COLUMNS if row.get(c)), "")
            source = str(row.get("Quelle (Kapitel)") or "")
            findings.append(Finding(
                key=key, category=category, number=number, description=description, source=source,
                previous=source.startswith(PREVIOUS_SOURCE_PREFIX), open=is_open_status(row.get("Status")),
                row=row, order=len(findings),
            ))
    return findings


def deterministic_groups(findings: List[Finding]) -> List[Decision]:
    """Rows with the same normalised text are one finding; the oldest one is kept."""
    by_text: Dict[str, List[Finding]] = {}
    for finding in findings:
        text = normalize_text(finding.description)
        if text:
            by_text.setdefault(text, []).append(finding)
    decisions = []
    for members in by_text.values():
        if len(members) < 2:
            continue
        keep = _oldest(members)
        decisions.append(Decision(
            ids=[m.key for m in members], behalten=keep.key, konfidenz=1.0,
            begruendung="Identischer Text.", quelle="deterministisch",
        ))
    return decisions


def _oldest(members: List[Finding]) -> Finding:
    """Previous-audit findings before new ones, then the lower number, then table order."""
    return min(members, key=lambda f: (not f.previous, f.number if f.number is not None else 10**9, f.order))


class ReportFinalizer:
    """Deduplicates, numbers and cross-references the findings of an assembled report."""

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        stage_config = load_asset_json(PROMPT_CONFIG_PATH)["stages"][STAGE_NAME]["duplikate"]
        self.prompt_template: str = stage_config["prompt"]
        self.schema = load_asset_json(stage_config["schema_path"])
        logging.info(f"Report Finalizer initialized (confidence threshold {FINALIZER_CONFIDENCE_THRESHOLD}).")

    # ------------------------------------------------------------------ files
    async def latest_report_path(self) -> str:
        """The newest assembled report under results/, by its date stamp; finals excluded."""
        names = await self.gcs_client.list_blob_names_async(f"{RESULTS_BASE}/{REPORT_FILE_PREFIX}")
        candidates = sorted(
            n for n in names
            if n.endswith(".json") and not n.endswith(f"{FINAL_REPORT_SUFFIX}.json")
        )
        if not candidates:
            raise FileNotFoundError(
                f"No report found under gs://{self.config.bucket_name}/{RESULTS_BASE}/{REPORT_FILE_PREFIX}*.json. "
                "Run --generate-report first or pass the report path to --finalize-report."
            )
        return candidates[-1]

    @staticmethod
    def output_path_for(report_path: str) -> str:
        """results/report_261019.json -> results/report_261019_final.json (idempotent on a final)."""
        stem = report_path[:-5] if report_path.endswith(".json") else report_path
        if stem.endswith(FINAL_REPORT_SUFFIX):
            stem = stem[: -len(FINAL_REPORT_SUFFIX)]
        return f"{stem}{FINAL_REPORT_SUFFIX}.json"

    @staticmethod
    def log_path_for(report_path: str) -> str:
        name = report_path.rsplit("/", 1)[-1]
        stem = name[:-5] if name.endswith(".json") else name
        if stem.endswith(FINAL_REPORT_SUFFIX):
            stem = stem[: -len(FINAL_REPORT_SUFFIX)]
        return f"{FINALIZER_BASE}/{stem}_finalize_log.json"

    # ------------------------------------------------------------------ model
    def _model_items(self, findings: List[Finding]) -> List[Dict[str, Any]]:
        return [
            {
                "id": f.key,
                "kategorie": f.category,
                "herkunft": "vorheriges Audit" if f.previous else "aktuelles Audit",
                "quelle": f.source,
                "status": f.row.get("Status"),
                "offen": f.open,
                "beschreibung": f.description,
            }
            for f in findings
        ]

    async def _model_groups(self, findings: List[Finding]) -> Tuple[Dict[str, Any], str]:
        """The model's duplicate groups, from the run directory when the input is unchanged."""
        items = self._model_items(findings)
        prompt = self.prompt_template.replace(
            "{findings_json}", json.dumps(items, ensure_ascii=False, indent=1)
        )
        key_material = json.dumps(
            {"prompt": prompt, "schema": self.schema, "model": self.ai_client.model,
             "thinking": self.ai_client._resolve_thinking_level()},
            sort_keys=True, ensure_ascii=False,
        )
        key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:16]
        cache_path = f"{FINALIZER_BASE}/{key}/duplicate_groups.json"
        try:
            cached = await self.gcs_client.read_json_async(cache_path)
            logging.info(f"Duplicate groups for this report already exist at {cache_path}; no model call.")
            return cached, "cache"
        except NotFound:
            pass

        logging.info(f"Asking the model for duplicate groups over {len(items)} findings...")
        response = await self.ai_client.generate_checked_json_response(
            prompt=prompt,
            json_schema=self.schema,
            request_context_log=f"{STAGE_NAME}: duplikate",
        )
        # Raw answer first, so a bug in the code below costs a re-parse and not a second call.
        await self.gcs_client.write_json_async(response, cache_path)
        return response, "modell"

    # ------------------------------------------------------------------ decisions
    @staticmethod
    def _resolve_key(findings_by_key: Dict[str, Finding], raw_id: Any) -> Optional[str]:
        """Maps an ID the model returned onto a sent key: exact first, by number if unambiguous."""
        raw = str(raw_id or "").strip()
        if raw in findings_by_key:
            return raw
        category, number = parse_finding_id(raw)
        if category is None:
            return None
        matches = [k for k, f in findings_by_key.items() if (f.category, f.number) == (category, number)]
        return matches[0] if len(matches) == 1 else None

    def _model_decisions(self, response: Dict[str, Any], findings_by_key: Dict[str, Finding]) -> List[Decision]:
        decisions = []
        for group in response.get("gruppen") or []:
            ids, unknown = [], []
            for raw in group.get("ids") or []:
                key = self._resolve_key(findings_by_key, raw)
                if key is None:
                    unknown.append(str(raw))
                elif key not in ids:
                    ids.append(key)
            keep = self._resolve_key(findings_by_key, group.get("behalten"))
            try:
                confidence = float(group.get("konfidenz"))
            except (TypeError, ValueError):
                confidence = 0.0
            decision = Decision(
                ids=ids + unknown, behalten=str(group.get("behalten") or ""), konfidenz=confidence,
                begruendung=str(group.get("begruendung") or ""), quelle="modell",
            )
            if unknown:
                decision.status = f"verworfen: unbekannte IDs {unknown}"
            elif len(ids) < 2:
                decision.status = "verworfen: weniger als zwei Feststellungen"
            elif keep is None or keep not in ids:
                decision.status = "verworfen: 'behalten' ist keine ID der Gruppe"
            else:
                decision.behalten = keep
            decisions.append(decision)
        return decisions

    @staticmethod
    def _apply(decisions: List[Decision], findings_by_key: Dict[str, Finding]) -> None:
        """Marks dropped and closed findings. Groups are applied in order; a member another
        group already resolved is skipped, and a kept finding that was itself folded away
        is followed to its survivor, so no row is resolved twice."""
        def survivor(key: str) -> str:
            seen = set()
            while key not in seen:
                seen.add(key)
                f = findings_by_key[key]
                nxt = f.folded_into or f.closed_by
                if nxt is None:
                    return key
                key = nxt
            return key

        for decision in decisions:
            if decision.status != "offen":
                continue
            if decision.konfidenz < FINALIZER_CONFIDENCE_THRESHOLD:
                decision.status = f"manuell pruefen: Konfidenz {decision.konfidenz:.2f} unter {FINALIZER_CONFIDENCE_THRESHOLD}"
                continue
            keep_key = survivor(decision.behalten)
            keep = findings_by_key[keep_key]
            members = [
                findings_by_key[k] for k in decision.ids
                if k != keep_key and findings_by_key[k].folded_into is None and findings_by_key[k].closed_by is None
            ]
            if not members:
                decision.status = "uebersprungen: alle Mitglieder bereits durch eine fruehere Gruppe aufgeloest"
                continue
            if keep_key != decision.behalten:
                decision.wirkung.append(f"'{decision.behalten}' ist bereits in '{keep_key}' aufgegangen; diese wird behalten")
            for member in members:
                if member.previous:
                    member.closed_by = keep_key
                    decision.wirkung.append(f"{member.key} (vorheriges Audit) geschlossen mit Verweis auf {keep_key}")
                else:
                    member.folded_into = keep_key
                    decision.wirkung.append(f"{member.key} (aktuelles Audit) entfaellt, Fundstelle verweist auf {keep_key}")
                    if keep.previous:
                        keep.refound = True
                        chapter = member.source or "?"
                        keep.row["Status"] = f"Offen (erneut festgestellt, Kapitel {chapter})"
                        decision.wirkung.append(f"{keep_key}: Status auf 'Offen (erneut festgestellt, Kapitel {chapter})' gesetzt")
            decision.status = "angewendet"

    @staticmethod
    def _close_not_refound(response: Dict[str, Any], findings_by_key: Dict[str, Finding]) -> Tuple[List[str], List[str]]:
        """Closes the open document-review findings of the previous audit that no finding of
        the current audit matched. The documents were reviewed in full, so a deviation that
        was not found again is gone; on-site findings are not touched, they are judged later.
        Returns (closed keys, IDs the model named that were not open previous findings)."""
        closed, ignored = [], []
        for raw in response.get("dokumentenpruefung_ids") or []:
            key = ReportFinalizer._resolve_key(findings_by_key, raw)
            f = findings_by_key.get(key) if key else None
            if f is None or not f.previous or not f.open:
                ignored.append(str(raw))
                continue
            if f.refound or f.closed_by or f.closed_not_refound:
                continue
            f.closed_not_refound = True
            f.row["Status"] = NOT_REFOUND_STATUS
            closed.append(f.key)
        return closed, ignored

    # ------------------------------------------------------------------ numbering and tables
    @staticmethod
    def _assign_numbers(findings: List[Finding]) -> Dict[str, str]:
        """Previous findings keep their number as written; new ones continue per category."""
        renumbered: Dict[str, str] = {}
        for category in FINDINGS_TABLES:
            in_category = [f for f in findings if f.category == category]
            highest = max((f.number for f in in_category if f.previous and f.number is not None), default=0)
            for f in in_category:
                if f.previous:
                    f.final_id = f.key.split("#")[0]
            new_ones = [f for f in in_category if not f.previous and not f.dropped]
            new_ones.sort(key=lambda f: (f.number if f.number is not None else 10**9, f.order))
            for f in new_ones:
                highest += 1
                f.final_id = f"{category}-{highest:02d}"
                if f.key.split("#")[0] != f.final_id:
                    renumbered[f.key] = f.final_id
        return renumbered

    @staticmethod
    def _final_id_of(findings_by_key: Dict[str, Finding], key: str) -> str:
        seen = set()
        while key not in seen:
            seen.add(key)
            f = findings_by_key[key]
            nxt = f.folded_into or f.closed_by
            if nxt is None:
                return f.final_id or f.key
            key = nxt
        return findings_by_key[key].final_id or key

    def _rebuild_tables(self, report: Dict[str, Any], findings: List[Finding], findings_by_key: Dict[str, Finding]) -> None:
        tables = report["bsiAuditReport"]["anhang"]["abweichungenUndEmpfehlungen"]
        for category, table_key in FINDINGS_TABLES.items():
            if table_key not in tables:
                continue
            kept = [f for f in findings if f.category == category and not f.dropped]
            for f in kept:
                f.row["Nummer"] = f.final_id
                if f.closed_by:
                    f.row["Status"] = f"Geschlossen, siehe {self._final_id_of(findings_by_key, f.closed_by)}"
            kept.sort(key=lambda f: (parse_finding_id(f.final_id)[1] or 10**9, f.order))
            tables[table_key].setdefault("table", {})["rows"] = [f.row for f in kept]

    # ------------------------------------------------------------------ chapter texts
    def _reference_chapters(self, report: Dict[str, Any], findings: List[Finding],
                            findings_by_key: Dict[str, Finding]) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Writes the final number into every Feststellung item outside chapter 7."""
        by_text: Dict[str, List[Finding]] = {}
        for f in findings:
            by_text.setdefault(normalize_text(f.description), []).append(f)
        references, unmatched = [], []

        def match(category: str, text: str) -> Tuple[Optional[Finding], str]:
            norm = normalize_text(text)
            exact = by_text.get(norm, [])
            same_category = [f for f in exact if f.category == category]
            if same_category:
                return same_category[0], "exakt"
            if exact:
                return exact[0], "exakt (andere Kategorie)"
            # The auditor appended a remark to one copy: the shorter text opens the longer one.
            for f in findings:
                row_norm = normalize_text(f.description)
                if len(row_norm) >= 40 and (norm.startswith(row_norm) or row_norm.startswith(norm)):
                    return f, "enthalten"
            best, best_ratio = None, 0.0
            for f in findings:
                ratio = difflib.SequenceMatcher(None, norm, normalize_text(f.description)).ratio()
                if ratio > best_ratio:
                    best, best_ratio = f, ratio
            if best is not None and best_ratio >= _CHAPTER_MATCH_RATIO:
                return best, f"aehnlich {best_ratio:.2f}"
            return None, ""

        def walk(node: Any, path: str) -> None:
            if isinstance(node, dict):
                if node.get("type") == "finding" and isinstance(node.get("findingText"), str):
                    m = _FINDING_TEXT_RE.match(node["findingText"])
                    if m and m.group(1) != "OK" and m.group(2).strip():
                        category, text = m.group(1), m.group(2).strip()
                        finding, how = match(category, text)
                        if finding is None:
                            unmatched.append(path)
                            logging.warning(f"No chapter 7.2 row matches the Feststellung at {path}; its number stays open.")
                        else:
                            final_id = self._final_id_of(findings_by_key, finding.key)
                            node["findingText"] = f"[{final_id}] {text}"
                            references.append({"path": path, "finding": finding.key, "final_id": final_id, "match": how})
                    return
                for k, v in node.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")

        root = report["bsiAuditReport"]
        for chapter, content in root.items():
            if chapter != "anhang":
                walk(content, f"bsiAuditReport.{chapter}")
        return references, unmatched

    # ------------------------------------------------------------------ orchestration
    async def finalize(self, report: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Returns the finalized report (the input dict, modified) and the decision log."""
        findings = collect_findings(report)
        findings_by_key = {f.key: f for f in findings}
        previous = sum(1 for f in findings if f.previous)
        logging.info(f"Chapter 7.2 holds {len(findings)} findings: {previous} from the previous audit, {len(findings) - previous} new.")

        decisions = deterministic_groups(findings)
        logging.info(f"{len(decisions)} group(s) resolved deterministically (identical text).")
        groups_source = "keine (keine Feststellungen)"
        response: Dict[str, Any] = {}
        if len(findings) >= 2:
            response, groups_source = await self._model_groups(findings)
            model_decisions = self._model_decisions(response, findings_by_key)
            logging.info(f"Model returned {len(model_decisions)} group(s) ({groups_source}).")
            decisions.extend(model_decisions)

        self._apply(decisions, findings_by_key)
        closed_not_refound, ignored_ids = self._close_not_refound(response, findings_by_key)
        previous_open = sum(1 for f in findings if f.previous and f.open)
        logging.info(
            f"{previous_open} previous finding(s) were open; {len(closed_not_refound)} document-review finding(s) "
            f"not met again are closed, {len(ignored_ids)} ID(s) named by the model were not open previous findings."
        )
        renumbered = self._assign_numbers(findings)
        self._rebuild_tables(report, findings, findings_by_key)
        references, unmatched = self._reference_chapters(report, findings, findings_by_key)

        applied = [d for d in decisions if d.status == "angewendet"]
        counts = {
            "findings": len(findings),
            "previous": previous,
            "new": len(findings) - previous,
            "groups_deterministic": sum(1 for d in decisions if d.quelle == "deterministisch"),
            "groups_model": sum(1 for d in decisions if d.quelle == "modell"),
            "groups_applied": len(applied),
            "groups_below_threshold": sum(1 for d in decisions if d.status.startswith("manuell")),
            "groups_rejected": sum(1 for d in decisions if d.status.startswith(("verworfen", "uebersprungen"))),
            "new_dropped": sum(1 for f in findings if f.dropped),
            "previous_closed": sum(1 for f in findings if f.closed_by),
            "previous_open": previous_open,
            "previous_closed_not_refound": len(closed_not_refound),
            "renumbered": len(renumbered),
            "chapter_references": len(references),
            "chapter_unmatched": len(unmatched),
        }
        logging.info(
            f"Finalize: {counts['groups_applied']} group(s) applied, {counts['groups_below_threshold']} below the "
            f"confidence threshold, {counts['groups_rejected']} rejected; {counts['new_dropped']} new finding(s) folded, "
            f"{counts['previous_closed']} previous finding(s) closed, {counts['renumbered']} renumbered, "
            f"{counts['chapter_references']} chapter reference(s) written, {counts['chapter_unmatched']} unmatched."
        )
        log = {
            "stage": STAGE_NAME,
            "groups_source": groups_source,
            "confidence_threshold": FINALIZER_CONFIDENCE_THRESHOLD,
            "counts": counts,
            "decisions": [d.as_dict() for d in decisions],
            "renumbering": renumbered,
            "closed_not_refound": closed_not_refound,
            "document_review_ids_ignored": ignored_ids,
            "chapter_references": references,
            "chapter_unmatched": unmatched,
            "checker": [e for e in self.ai_client.checker_log if e.get("stage") == STAGE_NAME],
        }
        return report, log

    async def run(self, report_path: Optional[str] = None) -> str:
        """Reads the report, finalizes it and writes report_<...>_final.json plus the log.
        Returns the output path."""
        current_stage.set(STAGE_NAME)
        input_path = report_path or await self.latest_report_path()
        output_path = self.output_path_for(input_path)
        logging.info(f"Finalizing report gs://{self.config.bucket_name}/{input_path} -> {output_path}")
        report = await self.gcs_client.read_json_async(input_path)
        if not ReportGenerator._is_report_structurally_valid(report):
            raise ValueError(f"{input_path} is not an assembled report (no 'bsiAuditReport' root).")

        report, log = await self.finalize(report)
        log.update({"input": input_path, "output": output_path})
        log_path = self.log_path_for(input_path)
        await self.gcs_client.write_json_async(log, log_path)
        logging.info(f"Finalize log written to {log_path}")

        if not ReportGenerator._validate_report_against_schema(report):
            raise ValueError("The finalized report failed schema validation; nothing was written (see the log above).")
        await self.gcs_client.write_json_async(report, output_path)
        logging.info(f"Final report saved to {output_path}")
        return output_path
