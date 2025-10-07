#!/usr/bin/env python3
import subprocess
import os
from collections import defaultdict


def analyze_loc():
    """
    Analyzes the lines of code (LoC) for all files in the git repository,
    categorized by file extension.
    """
    print("Analyzing Lines of Code (LoC) for tracked git files...")

    try:
        # Get a list of all files tracked by git
        result = subprocess.run(
            ['git', 'ls-files'],
            capture_output=True,
            text=True,
            check=True,
            encoding='utf-8'
        )
        all_files = result.stdout.strip().split('\n')
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Error: Could not execute 'git ls-files'. Is git installed and are you in a git repository? Details: {e}")
        return

    # This specific large JSON file is excluded from the count as requested.
    # We use endswith to make the script runnable from any subdirectory of the repo.
    excluded_file_suffix = 'assets/json/BSI_GS_OSCAL_current_2023_benutzerdefinierte.json'

    loc_by_extension = defaultdict(int)
    total_loc = 0

    max_lines = 1000  # Threshold for skipping large files
    for file_path in all_files:
        if file_path.endswith(excluded_file_suffix):
            print(f"--> Excluding file: {file_path}")
            continue

        try:
            # Use 'ignore' to handle potential binary files gracefully
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                line_count = sum(1 for _ in f)

            if line_count > max_lines:
                print(f"--> Skipping file (>{max_lines} lines): {file_path}")
                continue


            _, extension = os.path.splitext(file_path)
            category = extension if extension else '.no_extension'

            loc_by_extension[category] += line_count
            total_loc += line_count
        except FileNotFoundError:
            # This can happen if a file was deleted but the deletion isn't committed yet.
            print(f"--> Warning: File not found, skipping: {file_path}")

    # --- Print the report ---
    print("\n" + "="*50)
    print("      Lines of Code (LoC) by File Extension")
    print("="*50)
    sorted_extensions = sorted(loc_by_extension.items(), key=lambda item: item[1], reverse=True)
    for extension, count in sorted_extensions:
        print(f"{extension:<15} | {count:>10,} lines")
    print("-"*50)
    print(f"{'Total':<15} | {total_loc:>10,} lines")
    print("="*50)

if __name__ == "__main__":
    analyze_loc()