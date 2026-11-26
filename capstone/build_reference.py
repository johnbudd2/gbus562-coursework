"""One-time setup: build the derived reference data from the public files in reference/.

    python build_reference.py

1. Writes reference/cc_mcc.csv (every CC/MCC code and severity) from CMS Tables 6I/6J/6K.
2. Builds the Chroma search index of the ICD-10-CM guidelines in .chroma/.
3. Checks that the tables load and agree with each other.
No API calls. Safe to run again; it rebuilds both outputs.
"""

import sys

import reference

REQUIRED = [reference.CODES_FILE, reference.MCC_FILE, reference.CC_FILE,
            reference.EXCLUSIONS_FILE, reference.GUIDELINES_FILE]


def main():
    missing = [p.name for p in REQUIRED if not p.exists()]
    if missing:
        sys.exit(f"Missing reference files in {reference.REF_DIR}: {', '.join(missing)} "
                 "(see reference/SOURCES.md for where to download them)")
    print("1/3 Exporting CC/MCC table ...")
    reference.export_csv()
    print("2/3 Building guideline index (first run downloads a small embedding model) ...")
    try:
        reference.build_index()
    except PermissionError:
        sys.exit(f"The guideline index in {reference.DB_DIR} is in use by another program "
                 "(is app.py running?). Close it and run build_reference.py again. "
                 "The existing index was left unchanged.")
    print("3/3 Checking tables ...")
    reference.check()
    print("\nReference data ready.")


if __name__ == "__main__":
    main()
