"""Build the local UK index. Run once; query many times.

    python -m entitygraph.ingest --db uk.sqlite --psc-shards 1
"""
from __future__ import annotations

import argparse
import os
import sys

from . import uk


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="entitygraph.ingest")
    ap.add_argument("--db", default="uk.sqlite")
    ap.add_argument("--basic", action="store_true", help="ingest the 494 MB company snapshot")
    ap.add_argument("--psc-shards", type=int, default=0,
                    help="how many 78 MB PSC shards to ingest (1-33)")
    ap.add_argument("--keep-zip", help="directory to cache downloaded zips in")
    args = ap.parse_args(argv)

    if args.basic:
        url = uk.latest_basic_url()
        print(f"basic: {url}")
        n = uk.ingest_basic(args.db, url, os.path.join(args.keep_zip, "basic.zip") if args.keep_zip else None)
        print(f"  {n} companies")

    if args.psc_shards:
        urls = uk.latest_psc_urls()[: args.psc_shards]
        for u in urls:
            print(f"psc: {u}")
            n = uk.ingest_psc_shard(args.db, u, os.path.join(args.keep_zip, os.path.basename(u)) if args.keep_zip else None)
            print(f"  {n} PSC rows")

    print(uk.stats(args.db))
    return 0


if __name__ == "__main__":
    sys.exit(main())
