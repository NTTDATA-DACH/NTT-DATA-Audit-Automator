#!/bin/bash
#
# This script finds all non-binary files in the current directory and its
# subdirectories that are tracked by Git (respecting .gitignore), and
# concatenates them into a single context file.
#
# USAGE:
# 1. Place this script in the root of your project directory.
# 2. Make it executable: chmod +x create_context.sh
# 3. Run it: ./create_context.sh
#
# The output will be a file named 'project_context.txt'.

set -e # Exit immediately if a command fails

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
    if [[ "$(file -b --mime-type "$filename")" == text/* ]]; then
        echo "   Adding: $filename"
        
        # Append a header with the filename
        echo "==== ${filename} ====" >> "$OUTPUT_FILE"
        
        # Append the file's content
        cat "$filename" >> "$OUTPUT_FILE"
        
        # Add a newline for spacing between files
        echo "" >> "$OUTPUT_FILE"
    fi
done

echo ""
echo "✅ Project context successfully generated in: $OUTPUT_FILE"