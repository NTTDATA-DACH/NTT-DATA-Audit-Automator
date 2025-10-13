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

def update_json_records(target_file_path: str, mapping_file_path: str) -> None:
    """
    Updates records in a target JSON file based on a mapping JSON file.

    The function reads a mapping of 'name' to 'kuerzel' from the mapping file.
    It then iterates through the target file, and for each record, it uses the
    value of 'zielobjekt_kuerzel' as a key to find the corresponding 'name'
    in the mapping. If a match is found, it updates the record's
    'zielobjekt_kuerzel' and 'zielobjekt_name' with the values from the mapping.

    Args:
        target_file_path (str): The path to the JSON file to be updated.
        mapping_file_path (str): The path to the JSON file containing the mappings.

    Returns:
        None: The function modifies the target file in place.
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

        if lookup_key in lookup_map:
            mapping_entry = lookup_map[lookup_key]
            
            # This is an inline update to the record dictionary
            record['zielobjekt_kuerzel'] = mapping_entry['kuerzel']
            record['zielobjekt_name'] = mapping_entry['name']
            
            logging.info(f"Updated record '{record.get('id', 'N/A')}': set kuerzel to '{mapping_entry['kuerzel']}'.")
            updated_count += 1
        else:
            logging.warning(f"No mapping found for kuerzel/name '{lookup_key}' in record '{record.get('id', 'N/A')}'.")

    # Write the modified data back to the target file
    try:
        with open(target_file_path, 'w', encoding='utf-8') as f:
            # Use indent for readability and ensure_ascii=False for special characters
            json.dump(target_data, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logging.error(f"Failed to write updated content to '{target_file_path}': {e}")
        sys.exit(1)

    logging.info(f"Processing complete. Updated {updated_count} records in '{target_file_path}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Updates a target JSON file based on a mapping JSON file.",
        epilog="Example: python update_json_from_mapping.py anforderungen.json zielobjekte.json"
    )
    parser.add_argument(
        "target_file",
        help="The path to the JSON file to be updated."
    )
    parser.add_argument(
        "mapping_file",
        help="The path to the JSON file containing the mappings."
    )
    args = parser.parse_args()

    update_json_records(args.target_file, args.mapping_file)