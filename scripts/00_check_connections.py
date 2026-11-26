import os

import requests
from dotenv import load_dotenv

load_dotenv()

KOBO_SERVER = os.getenv("KOBO_SERVER")
KOBO_TOKEN = os.getenv("KOBO_TOKEN")
KOBO_REGISTRATION_UID = os.getenv("KOBO_REGISTRATION_UID")

AIRTABLE_TOKEN = os.getenv("AIRTABLE_TOKEN")
AIRTABLE_PROGRAMME_BASE_ID = os.getenv("AIRTABLE_PROGRAMME_BASE_ID")


def check_kobo():
    url = f"{KOBO_SERVER}/api/v2/assets/{KOBO_REGISTRATION_UID}/"
    headers = {"Authorization": f"Token {KOBO_TOKEN}"}
    response = requests.get(url, headers=headers, timeout=30)
    print("Kobo:", response.status_code, response.json().get("name"))


def check_airtable():
    url = f"https://api.airtable.com/v0/{AIRTABLE_PROGRAMME_BASE_ID}/partners"
    headers = {"Authorization": f"Bearer {AIRTABLE_TOKEN}"}
    response = requests.get(url, headers=headers, params={"maxRecords": 1}, timeout=30)
    print("Airtable:", response.status_code, len(response.json().get("records", [])), "record")


check_kobo()
check_airtable()