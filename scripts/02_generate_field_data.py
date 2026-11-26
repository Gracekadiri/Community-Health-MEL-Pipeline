from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = PROJECT_ROOT / "data" / "reference"
SEEDS_DIR = PROJECT_ROOT / "seeds"


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


if __name__ == "__main__":
    chws, schedule = load_reference_data()
    print(len(chws), "CHWs loaded")
    print(chws.head())
    print(schedule)