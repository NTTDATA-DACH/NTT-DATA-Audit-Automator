# bsi-audit-automator/src/audit/stages/gs_extraction/data_processor.py
import logging
from typing import List, Dict, Any, Tuple
from collections import defaultdict
from datetime import datetime


class DataProcessor:
    """Handles data processing operations including deduplication and quality scoring."""

    @staticmethod
    def deduplicate_requirements(all_anforderungen: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Deduplicates requirements based on (id, zielobjekt_kuerzel) composite key.
        For duplicates, selects the version with the highest quality score.
        
        Args:
            all_anforderungen: List of all extracted requirements (may contain duplicates)
            
        Returns:
            List of deduplicated requirements with highest quality versions retained
        """
        # Group requirements by composite key
        requirement_groups = defaultdict(list)
        for req in all_anforderungen:
            req_id = req.get('id')
            zielobjekt_kuerzel = req.get('zielobjekt_kuerzel')
            
            # Skip requirements missing critical identifiers
            if not req_id or not zielobjekt_kuerzel:
                logging.warning(f"Skipping requirement with missing ID or Zielobjekt: {req}")
                continue
                
            composite_key = (req_id, zielobjekt_kuerzel)
            requirement_groups[composite_key].append(req)
        
        deduplicated = []
        duplicate_count = 0
        
        for composite_key, req_list in requirement_groups.items():
            if len(req_list) == 1:
                # No duplicates for this key
                deduplicated.append(req_list[0])
            else:
                # Multiple versions found - select best quality
                duplicate_count += len(req_list) - 1
                best_req = DataProcessor._select_best_requirement_version(req_list)
                deduplicated.append(best_req)
                
                req_id, zielobjekt_kuerzel = composite_key
                logging.info(f"Resolved {len(req_list)} duplicates for requirement '{req_id}' on '{zielobjekt_kuerzel}'")
        
        logging.info(f"Deduplication complete: {duplicate_count} duplicates removed, {len(deduplicated)} unique requirements retained")
        return deduplicated

    @staticmethod
    def _calculate_quality_score(requirement: Dict[str, Any]) -> float:
        """
        Calculates a quality score for a requirement based on completeness and content quality.
        Higher scores indicate better extractions.
        
        Args:
            requirement: The requirement dictionary to score
            
        Returns:
            Quality score (0.0 to 1.0)
        """
        score = 0.0
        
        # Check for presence and quality of key fields
        umsetzungserlaeuterung = requirement.get('umsetzungserlaeuterung', '').strip()
        if umsetzungserlaeuterung and len(umsetzungserlaeuterung) > 10:
            if 'keine spezifische angabe' not in umsetzungserlaeuterung.lower():
                score += 0.4  # Good explanation content
            else:
                score += 0.1  # Generic/fallback explanation
        
        # Valid status increases score
        status = requirement.get('umsetzungsstatus', '').strip().lower()
        if status in ['ja', 'nein', 'teilweise', 'entbehrlich']:
            score += 0.3
        
        # Recent check date increases score
        date_str = requirement.get('datumLetztePruefung', '1970-01-01')
        if date_str != '1970-01-01':
            try:
                if '.' in date_str:
                    check_date = datetime.strptime(date_str, "%d.%m.%Y")
                else:
                    check_date = datetime.strptime(date_str, "%Y-%m-%d")
                
                # More recent dates get higher scores (within last 2 years = full points)
                days_old = (datetime.now() - check_date).days
                if days_old <= 730:  # 2 years
                    score += 0.2
                elif days_old <= 1460:  # 4 years
                    score += 0.1
            except ValueError:
                pass  # Invalid date format
        
        # Title presence
        if requirement.get('titel', '').strip():
            score += 0.1
        
        return min(score, 1.0)  # Cap at 1.0

    @staticmethod
    def _select_best_requirement_version(requirement_versions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Selects the best version from a list of duplicate requirements.
        
        Args:
            requirement_versions: List of requirement dictionaries (duplicates)
            
        Returns:
            The requirement version with the highest quality score
        """
        if len(requirement_versions) == 1:
            return requirement_versions[0]
        
        # Calculate quality scores for all versions
        scored_versions = []
        for req in requirement_versions:
            quality_score = DataProcessor._calculate_quality_score(req)
            scored_versions.append((quality_score, req))
        
        # Sort by quality score (highest first) and return best version
        scored_versions.sort(key=lambda x: x[0], reverse=True)
        best_req = scored_versions[0][1]
        
        return best_req

    @staticmethod
    def assemble_final_results(results: List[Tuple[str, str, Any]]) -> Dict[str, List[Dict]]:
        """
        Assemble final results from all processed groups with robust deduplication.
        
        Args:
            results: List of tuples (kuerzel, name, result_data)
            
        Returns:
            Dictionary with deduplicated anforderungen list
        """
        all_anforderungen = []
        successful_count = 0
        failed_count = 0
        
        for kuerzel, name, result_data in results:
            if result_data and "anforderungen" in result_data:
                for anforderung in result_data["anforderungen"]:
                    anforderung['zielobjekt_kuerzel'] = kuerzel
                    anforderung['zielobjekt_name'] = name
                    all_anforderungen.append(anforderung)
                successful_count += 1
            else:
                failed_count += 1
                logging.warning(f"No valid requirements extracted for Zielobjekt '{kuerzel}'")

        # Apply deduplication
        logging.info(f"Pre-deduplication: {len(all_anforderungen)} total requirements")
        deduplicated_anforderungen = DataProcessor.deduplicate_requirements(all_anforderungen)
        logging.info(f"Post-deduplication: {len(deduplicated_anforderungen)} unique requirements")

        logging.info(f"AI refinement completed: {successful_count} successful, {failed_count} failed")
        return {"anforderungen": deduplicated_anforderungen}