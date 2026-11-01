"""Build the recent-bill index without blocking normal writes."""
import argparse
import json
import os

from app.db import recent_bill_index_sql


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    sql = recent_bill_index_sql(concurrently=True)
    if not args.apply:
        print(json.dumps({'apply': False, 'sql': sql}))
        return
    import psycopg
    with psycopg.connect(os.environ['KLS_DATABASE_URL'], autocommit=True,
                         options='-c statement_timeout=600000 -c lock_timeout=10000') as c:
        c.execute(sql)
        valid = c.execute("""SELECT i.indisvalid,i.indisready FROM pg_index i
            JOIN pg_class x ON x.oid=i.indexrelid JOIN pg_namespace n ON n.oid=x.relnamespace
            WHERE n.nspname='public' AND x.relname='idx_bills_recent_validated_listing'""").fetchone()
        assert valid == (True, True), 'Index is not ready; do not deploy the page change'
    print(json.dumps({'index': 'idx_bills_recent_validated_listing', 'valid': True, 'concurrent': True}))


if __name__ == '__main__':
    main()
