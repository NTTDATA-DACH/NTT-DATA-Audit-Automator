import argparse
import json
import logging
import sys
from typing import Any, Dict, List

# Configure basic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)

def update_json_records(target_file_path: str, mapping_file_path: str, output_file_path: str) -> None:
    """
    Updates records in a target JSON file based on a mapping JSON file.

    The function reads a mapping of 'name' to 'kuerzel' from the mapping file.
    It then iterates through the target file, and for each record, it uses the
    value of 'zielobjekt_kuerzel' as a key. It first attempts an exact match
    against the 'name' in the mapping. If no exact match is found, it
    attempts to find a unique partial match. If a unique match is found,
    it updates the record's 'zielobjekt_kuerzel' and 'zielobjekt_name'.
    The updated data is written to a new output file.

    Args:
        target_file_path (str): The path to the source JSON file.
        mapping_file_path (str): The path to the JSON file containing the mappings.
        output_file_path (str): The path to write the updated JSON data to.

    Returns:
        None: The function writes the output to a new file.
    """
    try:
        # Load the mapping file and build a lookup dictionary for efficiency
        with open(mapping_file_path, 'r', encoding='utf-8') as f:
            mapping_data = json.load(f)

        # Create a dictionary mapping a name to its full object for O(1) lookups
        # Key: 'name', Value: {'kuerzel': ..., 'name': ...}
        lookup_map: Dict[str, Dict[str, str]] = {
            item['name']: item for item in mapping_data.get('zielobjekte', [])
        }
        logging.info(f"Successfully built lookup map with {len(lookup_map)} entries from '{mapping_file_path}'.")

    except FileNotFoundError:
        logging.error(f"Error: Mapping file not found at '{mapping_file_path}'.")
        sys.exit(1)
    except json.JSONDecodeError:
        logging.error(f"Error: Could not decode JSON from mapping file '{mapping_file_path}'.")
        sys.exit(1)
    except KeyError:
        logging.error("Error: Mapping file is missing expected keys like 'zielobjekte', 'name', or 'kuerzel'.")
        sys.exit(1)


    try:
        # Load the target file to be updated
        with open(target_file_path, 'r', encoding='utf-8') as f:
            target_data = json.load(f)

    except FileNotFoundError:
        logging.error(f"Error: Target file not found at '{target_file_path}'.")
        sys.exit(1)
    except json.JSONDecodeError:
        logging.error(f"Error: Could not decode JSON from target file '{target_file_path}'.")
        sys.exit(1)

    updated_count = 0
    anforderungen: List[Dict[str, Any]] = target_data.get('anforderungen', [])

    if not anforderungen:
        logging.warning("Target file does not contain an 'anforderungen' list or it is empty. No changes made.")
        return

    # Iterate through each record and update if a match is found
    for record in anforderungen:
        # The current 'zielobjekt_kuerzel' value is used as the lookup key,
        # which corresponds to a 'name' in the mapping file.
        lookup_key = record.get('zielobjekt_kuerzel')

        if not lookup_key:
            continue # Skip if the record has no kuerzel to look up

        mapping_entry = None
        # First, try for a direct, exact match (fastest and safest)
        if lookup_key in lookup_map:
            mapping_entry = lookup_map.get(lookup_key)
        else:
            # If no exact match, try a partial match (slower, use with caution)
            possible_matches = [
                map_key for map_key in lookup_map if lookup_key in map_key
            ]
            if len(possible_matches) == 1:
                matched_key = possible_matches[0]
                mapping_entry = lookup_map[matched_key]
                logging.warning(
                    f"Record '{record.get('id', 'N/A')}': No exact match for '{lookup_key}'. "
                    f"Using unique partial match against '{matched_key}'."
                )
            elif len(possible_matches) > 1:
                logging.error(
                    f"Record '{record.get('id', 'N/A')}': Ambiguous partial match for '{lookup_key}'. "
                    f"Found {len(possible_matches)} potential matches: {possible_matches}. Skipping update."
                )

        if mapping_entry:
            # This is an inline update to the record dictionary
            record['zielobjekt_kuerzel'] = mapping_entry['kuerzel']
            record['zielobjekt_name'] = mapping_entry['name']
            logging.info(f"Updated record '{record.get('id', 'N/A')}': set kuerzel to '{mapping_entry['kuerzel']}'.")
            updated_count += 1
        elif lookup_key: # Log only if there was a key to look up
            logging.warning(f"No exact or unique partial mapping found for '{lookup_key}' in record '{record.get('id', 'N/A')}'.")

    # Write the modified data to the new output file
    try:
        with open(output_file_path, 'w', encoding='utf-8') as f:
            # Use indent for readability and ensure_ascii=False for special characters
            json.dump(target_data, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logging.error(f"Failed to write updated content to '{output_file_path}': {e}")
        sys.exit(1)

    logging.info(f"Processing complete. Updated {updated_count} records. Output saved to '{output_file_path}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Updates a target JSON file based on a mapping JSON file and saves to a new file.",
        epilog="Example: python %(prog)s anforderungen.json zielobjekte.json anforderungen_updated.json"
    )
    parser.add_argument(
        "target_file",
        help="The path to the source JSON file."
    )
    parser.add_argument(
        "mapping_file",
        help="The path to the JSON file containing the mappings."
    )
    parser.add_argument(
        "output_file",
        help="The path to write the new, updated JSON file to."
    )
    args = parser.parse_args()

    update_json_records(args.target_file, args.mapping_file, args.output_file)