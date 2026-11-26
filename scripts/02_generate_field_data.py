from pathlib import Path
import random
from datetime import date, timedelta

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = PROJECT_ROOT / "data" / "reference"
SEEDS_DIR = PROJECT_ROOT / "seeds"

random.seed(42)
TODAY = date.today()
HISTORY_DAYS = 365

FIRST_NAMES_F = ["Aisha", "Hadiza", "Zainab", "Fatima", "Maryam", "Halima", "Rabi", "Amina", "Hauwa", "Khadija", "Safiya", "Jamila", "Sadiya", "Hafsat", "Nafisa"]
FIRST_NAMES_M = ["Musa", "Ibrahim", "Yusuf", "Abubakar", "Sani", "Bashir", "Aliyu", "Umar", "Kabiru", "Nasiru", "Lawal", "Shehu", "Haruna", "Bello", "Isa"]
FAMILY_NAMES = ["Bello", "Abdullahi", "Lawal", "Sani", "Usman", "Garba", "Idris", "Mohammed", "Shehu", "Suleiman", "Aliyu", "Yahaya", "Ibrahim", "Umar", "Musa", "Haruna", "Bala", "Adamu", "Tukur", "Dauda"]


def load_reference_data():
    facilities = pd.read_excel(REFERENCE_DIR / "master_facility_list.xlsx")
    chws = pd.read_excel(
        REFERENCE_DIR / "airtable_reference_tables.xlsx", sheet_name="chw_roster"
    )
    schedule = pd.read_csv(SEEDS_DIR / "vaccine_schedule.csv")

    chws = chws.merge(
        facilities[["facility_id", "state", "lga", "ward", "latitude", "longitude"]],
        on="facility_id",
        how="left",
    )
    return chws, schedule


def make_child(chw):
    latest_date = TODAY
    if pd.notna(chw["end_date"]):
        latest_date = pd.to_datetime(chw["end_date"]).date()

    registration_date = latest_date - timedelta(days=random.randint(1, HISTORY_DAYS))
    dob = registration_date - timedelta(days=random.randint(0, 300))
    sex = random.choice(["male", "female"])
    first_name = random.choice(FIRST_NAMES_F if sex == "female" else FIRST_NAMES_M)
    family_name = random.choice(FAMILY_NAMES)

    return {
        "child_id": f"CH-{random.getrandbits(32):08x}",
        "chw_id": chw["chw_id"],
        "facility_id": chw["facility_id"],
        "lga": chw["lga"],
        "registration_date": registration_date,
        "dob": dob,
        "sex": sex,
        "child_name": f"{first_name} {family_name}",
        "family_name": family_name,
    }


def generate_children(chws):
    children = []
    for _, chw in chws.iterrows():
        for _ in range(random.randint(10, 15)):
            children.append(make_child(chw))
    return pd.DataFrame(children)


if __name__ == "__main__":
    chws, schedule = load_reference_data()
    children = generate_children(chws)
    print(len(children), "children generated")
    print(children.head())