#!/usr/bin/env python3
"""Generate mock BSI source documents for smoke-testing the audit pipeline.

The pipeline classifies source documents into BSI categories by *filename*
(see rag_client._create_document_map), so each mock is named after the category
it represents. Documents must be valid PDFs: the Grundschutz-Check is OCR'd by
Document AI, the rest are sent to Vertex as PDF context parts.

This is a TEST FIXTURE generator — the content is intentionally minimal, so the
AI stages will run but produce meaningless findings. Use it to exercise plumbing
(SDK calls, batching, report assembly/validation), not output quality.

Usage:
    python scripts/make_mock_docs.py                       # write PDFs to ./mock_documents
    python scripts/make_mock_docs.py --out-dir /tmp/mocks
    python scripts/make_mock_docs.py --bucket my-bucket    # also upload to gs://my-bucket/source_documents/

Requires PyMuPDF (fitz), which is already a project dependency:
    pip install -r audit-automator/requirements.txt
"""
import argparse
import pathlib
import subprocess
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("ERROR: PyMuPDF (fitz) is not installed. Run: pip install -r audit-automator/requirements.txt")

# Category -> filename. The names must match the BSI categories the stages look
# for (stage_3 coverage check + per-stage source_categories).
CATEGORIES = [
    "Grundschutz-Check",        # mandatory for --run-gs-check-extraction (Document AI)
    "Sicherheitsleitlinie",
    "Strukturanalyse",
    "Schutzbedarfsfeststellung",
    "Modellierung",
    "Risikoanalyse",
    "Realisierungsplan",
    "Vorheriger-Auditbericht",  # only needed for --scan-previous-report
]

# A few fake Zielobjekt markers so the Grundschutz-Check extraction has structure
# to group on (a blank page yields the no-marker path: logged + skipped).
ZIELOBJEKTE = ["SVR-01", "APP-02", "NET-03"]


def _make_pdf(path: pathlib.Path, category: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    lines = [
        f"MOCK DOCUMENT — {category}",
        "BSI IT-Grundschutz audit test fixture (content is not meaningful).",
    ]
    if category == "Grundschutz-Check":
        # Give the marker-based grouper something to find: each Zielobjekt kuerzel
        # on its own line, followed by a fake requirement row.
        for z in ZIELOBJEKTE:
            lines += ["", z, f"Anforderung {z}.A1  Umsetzungsstatus: Ja  Datum: 2026-01-15"]
    page.insert_text((72, 72), "\n".join(lines), fontsize=11)
    doc.save(str(path))
    doc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", default="mock_documents", help="Local directory to write PDFs into (default: ./mock_documents).")
    parser.add_argument("--bucket", help="If set, upload the PDFs to gs://<bucket>/source_documents/ via gsutil.")
    args = parser.parse_args()

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for category in CATEGORIES:
        target = out_dir / f"{category}.pdf"
        _make_pdf(target, category)
        print(f"wrote {target}")
    print(f"\n{len(CATEGORIES)} mock PDFs written to {out_dir}/")

    if args.bucket:
        dest = f"gs://{args.bucket}/source_documents/"
        print(f"\nUploading to {dest} ...")
        subprocess.run(["gsutil", "-m", "cp", f"{out_dir}/*.pdf", dest], check=True)
        print("Upload complete.")
    else:
        print("\nTo upload, either re-run with --bucket <name> or:")
        print(f"    gsutil -m cp {out_dir}/*.pdf gs://<BUCKET_NAME>/source_documents/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
