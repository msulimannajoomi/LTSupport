"""Quick read-only look at the MongoDB Atlas database without needing a separate DB client.
Usage:
    python view_db.py                 # dump every collection
    python view_db.py users           # dump just one collection
"""
import sys

import db


def dump_collection(mongo_db, name):
    docs = list(mongo_db[name].find())
    print(f"\n--- {name} ({len(docs)} document(s)) ---")
    for doc in docs:
        print(doc)


def main():
    mongo_db = db._get_db()
    collections = sys.argv[1:] or ["users", "sessions", "devices"]
    for c in collections:
        dump_collection(mongo_db, c)


if __name__ == "__main__":
    main()
