import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(_THIS_DIR, "..")

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma")
PARSED_DIR = os.path.join(DATA_DIR, "parsed")
RAW_PDFS_DIR = os.path.join(DATA_DIR, "raw_pdfs")
CONFIG_DOMAINS_DIR = os.path.join(PROJECT_ROOT, "config", "domains")


def project_relative(path: str) -> str:
    """Path to store in data files: relative to the project, so the data keeps
    working after the folder is renamed or moved (or mounted into Docker)."""
    return os.path.relpath(path, PROJECT_ROOT).replace(os.sep, "/")