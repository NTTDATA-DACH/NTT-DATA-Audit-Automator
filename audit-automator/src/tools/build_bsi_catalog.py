"""
Build-time converter: official BSI XML-Kompendium 2023 -> the lean catalog the audit
pipeline reads at runtime (assets/json/bsi_kompendium_ed2023.json).

Run from the audit-automator/ directory:

    python -m src.tools.build_bsi_catalog            # downloads the pinned XML once
    python -m src.tools.build_bsi_catalog --offline  # requires the XML in .cache/ed23/

The generated JSON is committed. Runtime therefore needs neither network access nor an
XML parser, and the catalog cannot drift from the official edition unnoticed: the source
is sha256-pinned and the reference counts below are asserted on every build.
"""

import argparse
import datetime
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List

from src.tools.ed23_xml import (
    BSI_XML_URL,
    ED23_CACHE_DIR,
    fetch_official_xml,
    load_baustein_titles,
    load_official_xml,
)

DEFAULT_OUT_PATH = "assets/json/bsi_kompendium_ed2023.json"

# Reference counts of the pinned Edition-2023 file. They are part of the supply-chain gate:
# a source that still matches the sha256 pin must yield exactly these.
EXPECTED_BAUSTEINE = 111
EXPECTED_ANFORDERUNGEN_AKTIV = 1834
EXPECTED_ENTFALLEN = 290

CANONICAL_REQ_ID_RE = re.compile(r"^[A-Z]{2,7}(?:\.\d+)+\.A\d+$")
VALID_LEVELS = {"B", "S", "H"}

logger = logging.getLogger(__name__)


def _sort_key(identifier: str) -> List[Any]:
    """Natural sort for BSI IDs so APP.1.2 precedes APP.1.10 and .A2 precedes .A10."""
    parts: List[Any] = []
    for chunk in identifier.replace(".A", ".~").split("."):
        if chunk.startswith("~"):
            parts.append((2, int(chunk[1:])))
        elif chunk.isdigit():
            parts.append((1, int(chunk)))
        else:
            parts.append((0, chunk))
    return parts


def build_catalog(xml_bytes: bytes, sha256: str) -> Dict[str, Any]:
    """Parses the official XML and returns the lean catalog structure."""
    requirements, rejected = load_official_xml(xml_bytes)
    logger.info(
        f"XML geparst: {len(requirements)} Anforderungen, "
        f"{len(rejected)} anforderungsähnliche Titel verworfen (Gefährdungslage o. ä.)."
    )

    baustein_ids = {r["baustein"] for r in requirements.values()}
    baustein_titles = load_baustein_titles(xml_bytes, keep_ids=baustein_ids)
    missing_titles = sorted(baustein_ids - set(baustein_titles))
    if missing_titles:
        raise SystemExit(
            f"FEHLER: Für {len(missing_titles)} Baustein(e) wurde kein Titel im XML gefunden: "
            f"{missing_titles[:10]}"
        )

    anforderungen = [
        {
            "id": r["id"],
            "baustein": r["baustein"],
            "schicht": r["schicht"],
            "titel": r["titel"],
            "level": r["level"],
            "rolle": r["rolle"],
            "entfallen": r["entfallen"],
            "text": r["prose"],
        }
        for r in sorted(requirements.values(), key=lambda r: _sort_key(r["id"]))
    ]
    bausteine = [
        {"id": bid, "titel": baustein_titles[bid], "schicht": bid.split(".", 1)[0]}
        for bid in sorted(baustein_ids, key=_sort_key)
    ]

    aktiv = [a for a in anforderungen if not a["entfallen"]]
    entfallen = [a for a in anforderungen if a["entfallen"]]

    return {
        "meta": {
            "source_url": BSI_XML_URL,
            "sha256": sha256,
            "edition": "2023",
            "generated": datetime.date.today().isoformat(),
            "counts": {
                "bausteine": len(bausteine),
                "anforderungen_aktiv": len(aktiv),
                "entfallen": len(entfallen),
            },
        },
        "bausteine": bausteine,
        "anforderungen": anforderungen,
    }


def assert_invariants(catalog: Dict[str, Any]) -> None:
    """Fails the build loudly if the parsed catalog deviates from the pinned edition."""
    problems: List[str] = []
    counts = catalog["meta"]["counts"]
    for label, actual, expected in (
        ("bausteine", counts["bausteine"], EXPECTED_BAUSTEINE),
        ("anforderungen_aktiv", counts["anforderungen_aktiv"], EXPECTED_ANFORDERUNGEN_AKTIV),
        ("entfallen", counts["entfallen"], EXPECTED_ENTFALLEN),
    ):
        if actual != expected:
            problems.append(f"{label}: erwartet {expected}, ist {actual}")

    baustein_ids = {b["id"] for b in catalog["bausteine"]}
    bad_ids = [a["id"] for a in catalog["anforderungen"] if not CANONICAL_REQ_ID_RE.match(a["id"])]
    if bad_ids:
        problems.append(f"{len(bad_ids)} nicht-kanonische Anforderungs-IDs, z. B. {bad_ids[:5]}")

    bad_levels = [
        f"{a['id']}={a['level']!r}"
        for a in catalog["anforderungen"]
        if not a["entfallen"] and a["level"] not in VALID_LEVELS
    ]
    if bad_levels:
        problems.append(
            f"{len(bad_levels)} aktive Anforderungen ohne gültiges Level (B/S/H), "
            f"z. B. {bad_levels[:5]}"
        )

    orphans = [a["id"] for a in catalog["anforderungen"] if a["baustein"] not in baustein_ids]
    if orphans:
        problems.append(f"{len(orphans)} Anforderungen ohne Baustein-Eintrag, z. B. {orphans[:5]}")

    if problems:
        raise SystemExit("FEHLER: Katalog-Invarianten verletzt:\n  - " + "\n  - ".join(problems))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=DEFAULT_OUT_PATH, help=f"Zielpfad (Default: {DEFAULT_OUT_PATH})")
    parser.add_argument("--cache-dir", default=ED23_CACHE_DIR, help="Download-Cache für das XML")
    parser.add_argument("--offline", action="store_true", help="Nur den Cache nutzen, nicht laden")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    xml_bytes, sha256 = fetch_official_xml(cache_dir=args.cache_dir, offline=args.offline)
    catalog = build_catalog(xml_bytes, sha256)
    assert_invariants(catalog)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=1)
        f.write("\n")

    counts = catalog["meta"]["counts"]
    size_mb = os.path.getsize(args.out) / 1024 / 1024
    logger.info(
        f"Katalog geschrieben: {args.out} ({size_mb:.1f} MB) — "
        f"{counts['bausteine']} Bausteine, {counts['anforderungen_aktiv']} aktive Anforderungen, "
        f"{counts['entfallen']} entfallen."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
