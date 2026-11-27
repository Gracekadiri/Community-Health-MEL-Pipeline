"""
05_extract_kobo.py

Incremental extract of Kobo submissions into bronze.kobo_submissions.

Each run asks Kobo only for submissions received since the last run
(the watermark), upserts them, then moves the watermark forward.
Safe to rerun: records are upserted on their unique instance ID.
"""

import json
import os
from datetime import timezone

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

load_dotenv()

KOBO_SERVER = os.getenv("KOBO_SERVER").rstrip("/")
KOBO_TOKEN = os.getenv("KOBO_TOKEN")

# Each form and its Kobo ID (asset UID)
FORMS = {
    "registration": os.getenv("KOBO_REGISTRATION_UID"),
    "visit": os.getenv("KOBO_VISIT_UID"),
    "stock": os.getenv("KOBO_STOCK_UID"),
}
PAGE_SIZE = 1000
DEFAULT_START = "2000-01-01T00:00:00"   # used the first time, when no watermark exists yet

# Insert new records; update ones already loaded (upsert)
UPSERT_SQL = """
    INSERT INTO bronze.kobo_submissions
        (form_name, instance_id, kobo_id, submitted_at, payload, load_method)
    VALUES %s
    ON CONFLICT (form_name, instance_id) DO UPDATE
    SET payload = EXCLUDED.payload,
        submitted_at = EXCLUDED.submitted_at,
        loaded_at = now()
"""


def get_connection():
    """Connect to Postgres as etl_writer, the role allowed to write to bronze only."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user="etl_writer",
        password=os.getenv("ETL_WRITER_PASSWORD"),
    )


def fetch_submissions(asset_uid, since):
    """Get every submission received at or after `since`, oldest first, page by page."""
    url = f"{KOBO_SERVER}/api/v2/assets/{asset_uid}/data/"
    params = {
        "format": "json",
        "limit": PAGE_SIZE,
        "sort": json.dumps({"_submission_time": 1}),
        "query": json.dumps({"_submission_time": {"$gte": since}}),
    }
    headers = {"Authorization": f"Token {KOBO_TOKEN}"}

    records = []
    while url:
        response = requests.get(url, headers=headers, params=params, timeout=60)
        response.raise_for_status()
        page = response.json()
        records.extend(page["results"])
        url = page["next"]      # address of the next page, or None on the last page
        params = None           # the "next" address already contains the parameters
    return records


def get_watermark(cur, source):
    """Return the latest submission time already loaded for this source."""
    cur.execute("SELECT last_value FROM bronze.load_watermarks WHERE source = %s", (source,))
    row = cur.fetchone()
    if row is None:
        return DEFAULT_START
    return row[0].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def save_watermark(cur, source, value):
    """Create or move the watermark for this source."""
    cur.execute(
        """
        INSERT INTO bronze.load_watermarks (source, last_value)
        VALUES (%s, %s)
        ON CONFLICT (source) DO UPDATE
        SET last_value = EXCLUDED.last_value,
            updated_at = now()
        """,
        (source, value),
    )


def to_row(form_name, record):
    """Turn one Kobo record into a row for bronze.kobo_submissions."""
    return (
        form_name,                              # form_name
        record["meta/instanceID"],              # instance_id
        record.get("_id"),                      # kobo_id
        record["_submission_time"] + "+00:00",  # submitted_at (Kobo times are UTC)
        json.dumps(record),                     # payload: the whole record, untouched
        "api",                                  # load_method
    )


def extract_form(cur, form_name, asset_uid):
    """Extract one form: read watermark, fetch new records, upsert, move watermark."""
    source = f"kobo_{form_name}"
    since = get_watermark(cur, source)
    records = fetch_submissions(asset_uid, since)

    if records:
        rows = [to_row(form_name, r) for r in records]
        execute_values(cur, UPSERT_SQL, rows, page_size=1000)
        newest = max(r["_submission_time"] for r in records) + "+00:00"
        save_watermark(cur, source, newest)

    print(f"{form_name}: {len(records)} records since {since}")


def main():
    # One transaction: if anything fails, no rows are saved and no watermark moves.
    with get_connection() as conn:
        with conn.cursor() as cur:
            for form_name, asset_uid in FORMS.items():
                extract_form(cur, form_name, asset_uid)


if __name__ == "__main__":
    main()