#!/bin/bash
#
# This script finds all non-binary files in the current directory and its
# subdirectories that are tracked by Git (respecting .gitignore), and
# concatenates them into a single context file.
# Large files (>1000 lines) are truncated to the first 200 lines to keep
# the context file manageable.
# USAGE:
# 1. Place this script in the root of your project directory.
# 2. Make it executable: chmod +x create_context.sh
# 3. Run it: ./create_context.sh
#
# The output will be a file named 'project_context.txt'.

OUTPUT_FILE="project_context.txt"

# Check for required commands
if ! command -v git &> /dev/null; then
    echo "Error: 'git' command not found. This script must be run in a Git repository."
    exit 1
fi

if ! command -v file &> /dev/null; then
    echo "Error: 'file' command not found. This is required to identify text files."
    exit 1
fi

# Clear the output file to start fresh
> "$OUTPUT_FILE"

echo "🔍 Finding text files and generating context..."

# Use 'git ls-files' to get a list of all files tracked by git,
# respecting .gitignore. Pipe this list into a loop.
# --cached: All files tracked in the index.
# --others: All untracked files.
# --exclude-standard: Respects .gitignore, .git/info/exclude, and global gitignore.
git ls-files --cached --others --exclude-standard | while read -r filename; do
    # Check if the file is likely a text file by checking its MIME type.
    # This is more reliable than checking the file extension.
    if [[ "$(file -br --mime-type "$filename")" == text/* ]]; then
        # Append a header with the filename
        echo "==== ${filename} ====" >> "$OUTPUT_FILE"

        # Get line count to determine if we need to truncate
        line_count=$(wc -l < "$filename")

        # If file is too long, truncate it. Otherwise, add it whole.
        if [ "$line_count" -gt 1000 ]; then
            echo "   Adding: $filename (Truncated to 200 lines from $line_count)"
            head -n 200 "$filename" >> "$OUTPUT_FILE"
            echo "" >> "$OUTPUT_FILE"
            echo "[... File truncated. Only first 200 of ${line_count} lines included. ...]" >> "$OUTPUT_FILE"
        else
            echo "   Adding: $filename"
            cat "$filename" >> "$OUTPUT_FILE"
        fi

        # Add a newline for spacing between files
        echo "" >> "$OUTPUT_FILE"
    fi
done

echo ""
echo "✅ Project context successfully generated in: $OUTPUT_FILE"