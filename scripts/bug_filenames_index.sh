#!/bin/bash
#
# This script finds log lines matching "ESCAPED 2:", extracts the value that
# follows, creates a unique list of these values, and then searches for each
# value within a specified directory. It reports which values were not found.
#
# Usage:
# ./find_missing_log_values.sh <path_to_log_file> <directory_to_search>
#
# Example:
# ./find_missing_log_values.sh ./app.log ./source_documents/

# --- Argument Validation ---
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <path_to_log_file> <directory_to_search>"
    echo "Example: $0 ./app.log ./source_documents/"
    exit 1
fi

LOG_FILE="$1"
SEARCH_DIR="$2"

if [ ! -f "$LOG_FILE" ]; then
    echo "Error: Log file not found at '$LOG_FILE'" >&2
    exit 1
fi

if [ ! -d "$SEARCH_DIR" ]; then
    echo "Error: Search directory not found at '$SEARCH_DIR'" >&2
    exit 1
fi

# --- Main Logic ---
echo "🔍 Finding unique 'escaped' values from '$LOG_FILE'..."

# Use awk to find lines with "ESCAPED 2: " and print the text that follows.
# Then, pipe to `sort -u` to get a unique, sorted list of values.
unique_values=$(awk -F'ESCAPED 2: ' '/ESCAPED 2:/ {print $2}' "$LOG_FILE" | sort -u)

if [ -z "$unique_values" ]; then
    echo "No 'ESCAPED 2:' log lines found in '$LOG_FILE'."
    exit 0
fi

echo "✅ Found unique values. Now searching for them in '$SEARCH_DIR'..."
echo "---"

found_all=true
# Loop through each unique value.
# `while IFS= read -r` is a safe way to read lines.
# `<<< "$unique_values"` feeds the variable content into the loop.
while IFS= read -r value; do
    echo -n "  Checking '$value'... "
    # Search recursively (-r), for a fixed string (-F), and print only the matches (-o).
    # Then, count the number of lines (-l) to get the total occurrence count.
    match_count=$(grep -r -o -F -- "$value" "$SEARCH_DIR" | wc -l)

    if [ "$match_count" -gt 0 ]; then
        echo "✅ Found ($match_count times)"
    else
        echo "❌ Not Found"
        found_all=false
    fi
done <<< "$unique_values"

echo "---"
if [ "$found_all" = true ]; then
    echo "✅ All 'escaped' values were found in the search directory."
else
    echo "ℹ️ Search complete. Some values were not found as indicated above."
fi
