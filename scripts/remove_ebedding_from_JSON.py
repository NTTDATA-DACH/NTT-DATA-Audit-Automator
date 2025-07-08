import json
import os
import glob

def extract_unique_source_documents(input_dir):
    """
    Processes a directory of JSON Lines files, extracts 'source_document'
    values, and returns a set of unique values.

    Args:
        input_dir: Path to the directory containing JSON Lines files.

    Returns:
        A set of unique 'source_document' values.
    """
    unique_documents = set()
    json_files = glob.glob(os.path.join(input_dir, "*.json"))  # Find all .json files
    if not json_files:
        print(f"No .json files found in {input_dir}")
        return unique_documents

    for file_path in json_files:
        with open(file_path, 'r') as infile:
            for line in infile:
                try:
                    data = json.loads(line)
                    source_doc = data.get("source_document")
                    if source_doc:  # Only add if source_document exists and is not None
                        unique_documents.add(source_doc)
                except json.JSONDecodeError:
                    print(f"Skipping invalid JSON line in {file_path}: {line.strip()}")
                except Exception as e:
                    print(f"An error occurred while processing {file_path}: {e}")

    return unique_documents

def main():
    input_directory = "/home/christoph_puppe/test/"  # Replace with your directory path
    unique_docs = extract_unique_source_documents(input_directory)

    if unique_docs:
        print("Unique Source Documents:")
        for doc in unique_docs:
            print(doc)
    else:
        print("No unique source documents found.")

if __name__ == "__main__":
    main()
