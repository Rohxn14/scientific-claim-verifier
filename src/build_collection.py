"""Build (or resume building) a collection from its config/domains/<id>.yaml,
running the same pipeline the API runs in the background:
fetch -> parse (GROBID) -> chunk -> embed & index -> link citations.

    python build_collection.py efficient_attention
    python build_collection.py --list

Every stage is resumable, so re-running after a failure only does what's left.
"""
import argparse
import sys

from config_loader import list_domains, load_domain_config
import ingestion


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("domain", nargs="?", help="collection id (a file name in config/domains/)")
    parser.add_argument("--list", action="store_true", help="list configured collections and exit")
    args = parser.parse_args()

    if args.list or not args.domain:
        ready = {d["id"] for d in ingestion.ready_domains()}
        for d in sorted(list_domains()):
            print(f"  {d:28} {'ready' if d in ready else 'not built'}")
        return

    try:
        config = load_domain_config(args.domain)
    except FileNotFoundError as e:
        sys.exit(str(e))

    ingestion.load_jobs()
    ingestion.queue_job(args.domain, "started from the command line")
    ingestion.run_ingestion(args.domain, config)
    job = ingestion.JOB_STATUS[args.domain]
    print(f"\n[{args.domain}] {job['state']}: {job['detail']}")
    sys.exit(0 if job["state"] == "ready" else 1)


if __name__ == "__main__":
    main()
