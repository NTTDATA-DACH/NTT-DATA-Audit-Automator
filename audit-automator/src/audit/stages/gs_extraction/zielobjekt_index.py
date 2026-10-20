# bsi-audit-automator/src/audit/stages/gs_extraction/zielobjekt_index.py
"""One place that answers "which Zielobjekt does this identifier mean?".

Three sources name the same Zielobjekte with their own spelling: the Grundschutz-Check
(A.4), the system structure map the model reads out of A.1/A.3, and the audit plan in
chapter 4. GRC exports are not always clean: a customer may put a type word into the Kürzel
column ("Firewall", "GP", "Gruppe") and the real identifier into the name column ("FW-01",
"N-EXTERNERN"). A Kürzel shared by several Zielobjekte identifies none of them, so the parser gives
those objects the canonical Kürzel "<Kürzel> <Name>" and keeps the original spellings as
aliases. This index resolves any of the spellings (Kürzel, name, alias) to the canonical
Kürzel; an ambiguous spelling resolves to every candidate, never to a guess.
"""
from typing import Any, Dict, Iterable, List, Mapping, Set


def normalize_key(text: Any) -> str:
    """Comparison form of a Kürzel or name: case, umlauts, a trailing colon and spacing ignored."""
    return (str(text or "").strip().rstrip(":").strip().lower()
            .replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))


def canonical_kuerzel(kuerzel: str, name: str) -> str:
    """The Kürzel a Zielobjekt gets when its document Kürzel is shared with other Zielobjekte."""
    return f"{kuerzel.strip()} {name.strip()}".strip()


class ZielobjektIndex:
    """Resolves Kürzel, names and aliases to canonical Kürzel.

    Built from a registry of `{"kuerzel", "name", "aliases"?, "kuerzel_im_dokument"?}` entries,
    as the sheet parser writes it, or from the requirements themselves when no registry
    exists (the PDF extraction path).
    """

    def __init__(self, zielobjekte: Iterable[Mapping[str, Any]]):
        self._by_key: Dict[str, Set[str]] = {}
        self._names_by_document_kuerzel: Dict[str, Set[str]] = {}
        self.canonical: List[str] = []
        for z in zielobjekte:
            kuerzel = str(z.get("kuerzel") or "").strip()
            if not kuerzel:
                continue
            name = str(z.get("name") or "").strip()
            if kuerzel not in self.canonical:
                self.canonical.append(kuerzel)
            for key in (kuerzel, name, *(z.get("aliases") or [])):
                norm = normalize_key(key)
                if norm:
                    self._by_key.setdefault(norm, set()).add(kuerzel)
            document_kuerzel = str(z.get("kuerzel_im_dokument") or kuerzel).strip()
            if name:
                self._names_by_document_kuerzel.setdefault(document_kuerzel, set()).add(name)

    @classmethod
    def from_anforderungen(cls, anforderungen: Iterable[Mapping[str, Any]]) -> "ZielobjektIndex":
        """Registry derived from the (kuerzel, name) pairs the requirements carry."""
        return cls(zielobjekte_from_anforderungen(anforderungen))

    def resolve(self, key: Any) -> Set[str]:
        """Canonical Kürzel the identifier may mean: empty = unknown, several = ambiguous."""
        return set(self._by_key.get(normalize_key(key), ()))

    def is_ambiguous(self, key: Any) -> bool:
        return len(self.resolve(key)) > 1

    def collisions(self) -> Dict[str, List[str]]:
        """Document Kürzel shared by several Zielobjekte, with the names they stand for."""
        return {k: sorted(v) for k, v in sorted(self._names_by_document_kuerzel.items()) if len(v) > 1}


def zielobjekte_from_anforderungen(anforderungen: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Registry entries derived from the (kuerzel, name) pairs the requirements carry."""
    seen: Dict[tuple, Dict[str, Any]] = {}
    for a in anforderungen:
        kuerzel = str(a.get("zielobjekt_kuerzel") or "").strip()
        name = str(a.get("zielobjekt_name") or "").strip()
        if kuerzel and (kuerzel, name) not in seen:
            seen[(kuerzel, name)] = {"kuerzel": kuerzel, "name": name}
    return list(seen.values())
