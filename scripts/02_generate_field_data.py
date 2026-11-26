"""Generate CHW field data for the routine immunisation programme.

The programme is generated once, deterministically, from WORLD_START to WORLD_END:
births, registrations by CHWs, vaccination visits, missed doses and weekly facility
stock reports, including realistic data problems (duplicates, typos, late syncs).
Each record gets the date and time it was synced to KoboToolbox.

Modes
-----
history : write every record synced up to today as JSON lines in data/generated/history/,
          shaped like the KoboToolbox API output, for a one-off bulk load into bronze.
daily   : submit records synced since the last run to KoboToolbox through its API.
          Use --dry-run to see what would be sent without sending it.

Usage
-----
python scripts/02_generate_field_data.py --mode history
python scripts/02_generate_field_data.py --mode daily --dry-run
python scripts/02_generate_field_data.py --mode daily
"""
import argparse
import json
import os
import random
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests
from dotenv import load_dotenv

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = PROJECT_ROOT / "data" / "reference"
SEEDS_DIR = PROJECT_ROOT / "seeds"
OUTPUT_DIR = PROJECT_ROOT / "data" / "generated"
STATE_FILE = OUTPUT_DIR / "state.json"

SEED = 42
WORLD_START = date(2025, 12, 1)
WORLD_END = date(2027, 12, 31)
OLDEST_BIRTH = WORLD_START - timedelta(days=700)
LAGOS = timezone(timedelta(hours=1))

FORMS = {
    "registration": "KOBO_REGISTRATION_UID",
    "visit": "KOBO_VISIT_UID",
    "stock": "KOBO_STOCK_UID",
}

FIRST_NAMES_F = ["Aisha", "Hadiza", "Zainab", "Fatima", "Maryam", "Halima", "Rabi", "Amina", "Hauwa",
                 "Khadija", "Safiya", "Jamila", "Sadiya", "Hafsat", "Nafisa", "Bilkisu", "Ummi", "Habiba"]
FIRST_NAMES_M = ["Musa", "Ibrahim", "Yusuf", "Abubakar", "Sani", "Bashir", "Aliyu", "Umar", "Kabiru",
                 "Nasiru", "Lawal", "Shehu", "Haruna", "Bello", "Isa", "Mustapha", "Tijjani", "Garba"]
FAMILY_NAMES = ["Bello", "Abdullahi", "Lawal", "Sani", "Usman", "Garba", "Idris", "Mohammed", "Shehu",
                "Suleiman", "Aliyu", "Yahaya", "Ibrahim", "Umar", "Musa", "Haruna", "Bala", "Adamu",
                "Tukur", "Dauda", "Jibrin", "Salisu", "Kabir", "Ismail"]
NAME_VARIANTS = {"Aisha": "Aishatu", "Fatima": "Fatimah", "Mohammed": "Muhammad", "Hauwa": "Hawa",
                 "Maryam": "Mariam", "Yusuf": "Yusuph", "Usman": "Usmanu", "Abdullahi": "Abdullah",
                 "Halima": "Halimatu", "Zainab": "Zainabu", "Umar": "Umaru", "Musa": "Musah",
                 "Suleiman": "Sulaiman", "Idris": "Idrisu", "Haruna": "Harun"}
SETTLEMENTS = ["Tudun Wada", "Sabon Gari", "Unguwar Sarki", "Unguwar Liman", "Gidan Dutse",
               "Kofar Fada", "Unguwar Rimi", "Dan Maigari", "Gidan Kara", "Unguwar Gabas"]

# Programme performance differs by place and season.
LGA_FACTOR = {"Katsina": 1.05, "Dutse": 1.03, "Gusau": 1.0, "Batagarawa": 0.98, "Malumfashi": 0.95,
              "Gwaram": 0.93, "Jahun": 0.92, "Jibia": 0.85, "Maru": 0.8}
RAINY_MONTHS = {7, 8, 9}
MISSED_REASONS = (["caregiver_absent", "refused", "child_sick", "facility_closed",
                   "distance_transport", "family_permission", "insecurity", "other"],
                  [30, 10, 15, 8, 15, 10, 7, 5])
ANTIGEN = {"penta1": "penta", "penta3": "penta", "mcv1": "measles", "mcv2": "measles"}


# ----------------------------------------------------------------------------
# Reference data
# ----------------------------------------------------------------------------
def load_reference_data():
    facilities = pd.read_excel(REFERENCE_DIR / "master_facility_list.xlsx")
    chws = pd.read_excel(REFERENCE_DIR / "airtable_reference_tables.xlsx", sheet_name="chw_roster")
    targets = pd.read_excel(REFERENCE_DIR / "airtable_reference_tables.xlsx", sheet_name="phc_targets")
    schedule = pd.read_csv(SEEDS_DIR / "vaccine_schedule.csv").sort_values("sort_order")

    chws = chws.merge(facilities[["facility_id", "state", "lga", "ward", "latitude", "longitude"]],
                      on="facility_id", how="left")
    for col in ["start_date", "end_date"]:
        chws[col] = pd.to_datetime(chws[col]).dt.date
    programme_facilities = facilities[facilities.facility_id.isin(chws.facility_id)]
    other_facilities = facilities[~facilities.facility_id.isin(chws.facility_id)]
    return chws, targets, schedule, programme_facilities, other_facilities


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def ward_code(lga, ward_label):
    """'Katsina Ward 03' -> 'katsina_w03', the choice name used in the Kobo form."""
    number = int(str(ward_label).split()[-1])
    return f"{lga.lower().replace(' ', '_')}_w{number:02d}"


def gps(lat, lon, rng, spread):
    return f"{lat + rng.uniform(-spread, spread):.6f} {lon + rng.uniform(-spread, spread):.6f} 0 0"


def sync_time(event_day, rng):
    """When the CHW's phone synced the record: mostly the same evening, sometimes days later."""
    roll = rng.random()
    delay = 0 if roll < 0.6 else rng.randint(1, 3) if roll < 0.85 else rng.randint(4, 10)
    moment = datetime.combine(event_day + timedelta(days=delay), datetime.min.time(), LAGOS)
    return moment + timedelta(hours=rng.randint(15, 21), minutes=rng.randint(0, 59), seconds=rng.randint(0, 59))


def variant(name, rng):
    first, *rest = name.split()
    parts = [NAME_VARIANTS.get(first, first)] + [NAME_VARIANTS.get(p, p) if rng.random() < 0.5 else p for p in rest]
    return " ".join(parts)


def typo(child_id, rng):
    chars = list(child_id)
    i = rng.randint(3, len(chars) - 1)
    chars[i] = rng.choice("0123456789abcdef".replace(chars[i], ""))
    return "".join(chars)


def active_chw(facility_chws, day, rng):
    pool = facility_chws[(facility_chws.start_date <= day) &
                         (facility_chws.end_date.isna() | (facility_chws.end_date > day))]
    if pool.empty:
        pool = facility_chws
    return pool.iloc[rng.randrange(len(pool))]


# ----------------------------------------------------------------------------
# Generate the programme
# ----------------------------------------------------------------------------
def generate_world(chws, targets, schedule, programme_facilities, other_facilities):
    rng = random.Random(SEED)
    records = []          # (form, synced_at, payload)
    doses_given = []      # (facility_id, antigen, day) for stock calculations

    # Stockouts: short runs of weeks where a facility has no penta or measles vaccine.
    stockouts = set()
    first_monday = WORLD_START - timedelta(days=WORLD_START.weekday())
    weeks = [first_monday + timedelta(weeks=i) for i in range((WORLD_END - first_monday).days // 7 + 1)]
    for fac in sorted(chws.facility_id.unique()):
        for antigen in ["penta", "measles"]:
            for wk in weeks:
                if rng.random() < 0.02:
                    for k in range(rng.randint(1, 2)):
                        stockouts.add((fac, antigen, wk + timedelta(weeks=k)))

    def in_stockout(fac, milestone, day):
        monday = day - timedelta(days=day.weekday())
        return (fac, ANTIGEN[milestone], monday) in stockouts

    # CHWs whose phones were broken for a while: their records sync late, in a burst.
    silent = {}
    for chw_id in rng.sample(sorted(chws.chw_id), 10):
        start = WORLD_START + timedelta(days=rng.randint(30, 600))
        silent[chw_id] = (start, start + timedelta(days=rng.randint(14, 28)))

    def synced(chw_id, day):
        window = silent.get(chw_id)
        if window and window[0] <= day <= window[1]:
            moment = datetime.combine(window[1] + timedelta(days=rng.randint(0, 3)), datetime.min.time(), LAGOS)
            return moment + timedelta(hours=rng.randint(9, 20), minutes=rng.randint(0, 59))
        return sync_time(day, rng)

    def form_times(day):
        start = datetime.combine(day, datetime.min.time(), LAGOS) + timedelta(hours=rng.randint(8, 16),
                                                                              minutes=rng.randint(0, 59))
        return start.isoformat(timespec="milliseconds"), \
            (start + timedelta(minutes=rng.randint(3, 15))).isoformat(timespec="milliseconds")

    milestones = schedule.to_dict("records")
    chws_by_facility = {f: g for f, g in chws.groupby("facility_id")}
    target_lookup = targets.set_index(["facility_id", "year"])["annual_target_infants"].to_dict()
    fac_factor = {f: rng.uniform(0.85, 1.05) for f in sorted(chws.facility_id.unique())}

    for fac in sorted(chws_by_facility):
        fac_chws = chws_by_facility[fac]
        lga = fac_chws.iloc[0].lga
        fac_lat, fac_lon = fac_chws.iloc[0].latitude, fac_chws.iloc[0].longitude

        # Births: about the annual infant target each year, spread over the year.
        births = []
        day = OLDEST_BIRTH
        while day <= WORLD_END:
            year_end = min(date(day.year, 12, 31), WORLD_END)
            yearly = target_lookup.get((fac, min(max(day.year, 2025), 2027)), 0)
            n = round(yearly * rng.uniform(0.9, 1.1) * ((year_end - day).days + 1) / 365)
            births += [day + timedelta(days=rng.randint(0, (year_end - day).days)) for _ in range(n)]
            day = year_end + timedelta(days=1)

        for dob in sorted(births):
            if rng.random() > 0.92:          # CHWs never find some children
                continue
            if dob < WORLD_START:
                reg = WORLD_START + timedelta(days=rng.randint(0, 75))
            else:
                roll = rng.random()
                lag = rng.randint(0, 14) if roll < 0.6 else rng.randint(15, 60) if roll < 0.9 else rng.randint(61, 200)
                reg = dob + timedelta(days=lag)
            if reg > WORLD_END or (reg - dob).days > 700:
                continue

            chw = active_chw(fac_chws, reg, rng)
            sex = rng.choice(["male", "female"])
            family = rng.choice(FAMILY_NAMES)
            child_name = f"{rng.choice(FIRST_NAMES_F if sex == 'female' else FIRST_NAMES_M)} {family}"
            child_id = f"CH-{rng.getrandbits(32):08x}"
            hard_to_reach = rng.random() < 0.3
            p = (0.45 if hard_to_reach else 0.88) * LGA_FACTOR.get(lga, 0.95) * fac_factor[fac]
            hh_lat, hh_lon = fac_lat + rng.uniform(-0.04, 0.04), fac_lon + rng.uniform(-0.04, 0.04)
            relationship = rng.choices(["mother", "father", "grandparent", "other"], [85, 5, 7, 3])[0]
            caregiver = f"{rng.choice(FIRST_NAMES_M if relationship == 'father' else FIRST_NAMES_F)} {family}"
            has_phone = rng.random() < 0.7
            phone = f"0800{rng.randint(0, 9999999):07d}" if has_phone else None
            ward_label = chw.ward if rng.random() < 0.8 else f"{lga} Ward {rng.randint(1, 11):02d}"

            # Exit: a few families move away, and a few children die.
            exit_day, exit_status = None, None
            roll = rng.random()
            if roll < 0.006 or roll > 0.998:
                exit_day = reg + timedelta(days=rng.randint(30, 500))
                exit_status = "moved" if roll < 0.006 else "died"

            # Doses: decide when (if ever) each milestone was given.
            given = {}
            for m in milestones:
                due = dob + timedelta(days=int(m["due_age_days"]))
                earliest = due
                if isinstance(m["previous_milestone"], str):
                    prev = given.get(m["previous_milestone"])
                    if prev is None:
                        continue
                    earliest = max(due, prev + timedelta(days=int(m["min_days_after_previous"])))
                season = 0.85 if earliest.month in RAINY_MONTHS else 1.0
                if rng.random() < min(p * season, 0.97):
                    when = earliest + timedelta(days=min(int(rng.expovariate(1 / 12)), 150))
                    if when <= dob + timedelta(days=int(m["max_age_days"])):
                        given[m["milestone"]] = when

            prior = {m: d for m, d in given.items() if d < reg}
            source = "none" if not prior else ("card" if rng.random() < 0.6 else "recall")
            start, end = form_times(reg)
            dob_known = rng.random() < 0.9
            reg_payload = {
                "start": start, "end": end,
                "registration_date": reg.isoformat(), "lga": lga, "facility_id": fac,
                "chw_id": chw.chw_id, "ward": ward_code(lga, ward_label),
                "settlement": rng.choice(SETTLEMENTS), "household_gps": gps(hh_lat, hh_lon, rng, 0.0005),
                "child_name": child_name, "sex": sex, "dob_known": "yes" if dob_known else "no",
                "caregiver_name": caregiver, "caregiver_relationship": relationship,
                "has_phone": "yes" if has_phone else "no",
                "consent_followup": "yes" if rng.random() < 0.95 else "no",
                "history/history_source": source,
                "child_id": child_id, "zero_dose": "yes" if source == "none" else "no",
            }
            if dob_known:
                reg_payload["dob"] = dob.isoformat()
            else:
                reg_payload["age_months_est"] = str(max(0, min(23, (reg - dob).days // 30 + rng.randint(-1, 1))))
            if has_phone:
                reg_payload["caregiver_phone"] = phone
            if prior:
                reg_payload["history/prior_doses"] = " ".join(prior)
                if source == "card":
                    for m, d in prior.items():
                        reg_payload[f"history/{m}_date"] = d.isoformat()
            if rng.random() < 0.002:   # impossible: born after registration
                reg_payload["dob"] = (reg + timedelta(days=rng.randint(5, 40))).isoformat()
            records.append(("registration", synced(chw.chw_id, reg), reg_payload))

            # Some children are registered twice, with spelling differences.
            duplicate_id = None
            if rng.random() < 0.025:
                dup_reg = reg + timedelta(days=rng.randint(10, 120))
                if dup_reg <= WORLD_END:
                    dup_chw = active_chw(fac_chws, dup_reg, rng)
                    duplicate_id = f"CH-{rng.getrandbits(32):08x}"
                    dup = dict(reg_payload)
                    dup.update({"child_id": duplicate_id, "registration_date": dup_reg.isoformat(),
                                "chw_id": dup_chw.chw_id, "child_name": variant(child_name, rng),
                                "caregiver_name": variant(caregiver, rng)})
                    if "dob" in dup:
                        dup["dob"] = (dob + timedelta(days=rng.randint(-10, 10))).isoformat()
                    dup["start"], dup["end"] = form_times(dup_reg)
                    records.append(("registration", synced(dup_chw.chw_id, dup_reg), dup))

            # Visits after registration.
            drop_penta1 = rng.random() < 0.003

            def add_visit(day, doses, status, reason=None, visit_type=None):
                if day > WORLD_END or (exit_day and day > exit_day):
                    return
                vchw = active_chw(fac_chws, day, rng)
                vtype = visit_type or rng.choices(["fixed_session", "outreach_session", "home_visit"], [50, 25, 25])[0]
                vid = duplicate_id if duplicate_id and rng.random() < 0.3 and day > reg + timedelta(days=120) else child_id
                if rng.random() < 0.015:
                    vid = typo(vid, rng)
                vfac = fac
                if rng.random() < 0.003:
                    vfac = other_facilities.iloc[rng.randrange(len(other_facilities))].facility_id
                if vtype in ("home_visit", "defaulter_tracing"):
                    point = gps(hh_lat, hh_lon, rng, 0.001)
                elif vtype == "fixed_session":
                    point = gps(fac_lat, fac_lon, rng, 0.001)
                else:
                    point = gps(fac_lat, fac_lon, rng, 0.05)
                if rng.random() < 0.005:
                    point = gps(fac_lat + 0.4, fac_lon - 0.3, rng, 0.05)
                start, end = form_times(day)
                payload = {"start": start, "end": end, "visit_date": day.isoformat(), "lga": lga,
                           "facility_id": vfac, "chw_id": vchw.chw_id, "child_id": vid,
                           "child_name_check": child_name if rng.random() < 0.85 else variant(child_name, rng),
                           "visit_type": vtype, "child_status": status, "visit_gps": point}
                if status == "present":
                    payload["doses_given"] = " ".join(doses) if doses else "none"
                    payload["dose_was_due"] = "yes" if reason else "no"
                if reason:
                    payload["missed_reason"] = reason
                if status in ("present", "absent"):
                    payload["next_appointment"] = (day + timedelta(days=rng.choice([7, 14, 28]))).isoformat()
                records.append(("visit", synced(vchw.chw_id, day), payload))
                if rng.random() < 0.005:   # the same visit submitted twice
                    records.append(("visit", synced(vchw.chw_id, day), dict(payload)))
                for d in doses:
                    doses_given.append((vfac, ANTIGEN[d], day))

            for m in milestones:
                name = m["milestone"]
                due = dob + timedelta(days=int(m["due_age_days"]))
                if due < reg or due > WORLD_END:
                    continue
                when = given.get(name)
                if when is not None and when >= reg:
                    if (when - due).days > 28:
                        add_visit(due + timedelta(days=28 + rng.randint(0, 7)), [], rng.choice(["present", "absent"]),
                                  rng.choices(*MISSED_REASONS)[0], "defaulter_tracing")
                    if in_stockout(fac, name, when):
                        add_visit(when, [], "present", "vaccine_unavailable")
                        when = when + timedelta(days=rng.randint(7, 14))
                    if rng.random() < 0.004:   # impossible: dose recorded before minimum age
                        when = dob + timedelta(days=max(1, int(m["min_age_days"]) - rng.randint(5, 20)))
                    if name == "penta1" and drop_penta1:
                        continue
                    add_visit(when, [name], "present")
                elif when is None and (m["previous_milestone"] != m["previous_milestone"] or
                                       given.get(m["previous_milestone"]) is not None):
                    for k in range(rng.randint(1, 2)):
                        status = "absent" if rng.random() < 0.4 else "present"
                        reason = "caregiver_absent" if status == "absent" else rng.choices(*MISSED_REASONS)[0]
                        add_visit(due + timedelta(days=rng.randint(7, 21) + 30 * k), [], status, reason,
                                  "defaulter_tracing" if k else None)

            if exit_day and exit_day <= WORLD_END:
                add_visit(exit_day, [], exit_status, visit_type="home_visit")

    # Weekly stock reports per facility.
    used = {}
    for fac, antigen, day in doses_given:
        monday = day - timedelta(days=day.weekday())
        used[(fac, antigen, monday)] = used.get((fac, antigen, monday), 0) + 1
    fridge = targets.drop_duplicates("facility_id").set_index("facility_id")["has_vaccine_fridge"].to_dict()
    lga_of = chws.drop_duplicates("facility_id").set_index("facility_id")["lga"].to_dict()

    for fac in sorted(chws_by_facility):
        balance = {"penta": 40, "measles": 30}
        reporter = f"{rng.choice(FIRST_NAMES_F + FIRST_NAMES_M)} {rng.choice(FAMILY_NAMES)}"
        for wk in weeks:
            report_day = wk + timedelta(days=7)
            if report_day > WORLD_END:
                break
            start, end = form_times(report_day)
            payload = {"start": start, "end": end, "week_start": wk.isoformat(), "lga": lga_of[fac],
                       "facility_id": fac, "reporter_name": reporter,
                       "has_fridge": "yes" if fridge[fac] else "no"}
            for antigen in ["penta", "measles"]:
                n_used = used.get((fac, antigen, wk), 0)
                opening = balance[antigen]
                out = (fac, antigen, wk) in stockouts
                received = 0
                if not out and (wk.day <= 7 or opening < 10):
                    received = max(0, 60 - opening) // 10 * 10
                waste_rate = rng.uniform(0.03, 0.12) if antigen == "penta" else rng.uniform(0.1, 0.3)
                wasted = round(n_used * waste_rate) + (rng.randint(0, 2) if antigen == "measles" and n_used else 0)
                closing = 0 if out else max(0, opening + received - n_used - wasted)
                reported_closing = closing + (rng.randint(-5, 5) if rng.random() < 0.04 else 0)
                prefix = f"stock_{antigen}/{antigen}"
                payload.update({f"{prefix}_opening": str(opening), f"{prefix}_received": str(received),
                                f"{prefix}_used": str(n_used), f"{prefix}_wasted": str(wasted),
                                f"{prefix}_closing": str(max(0, reported_closing)),
                                f"{prefix}_stockout_days": str(rng.randint(2, 7) if out else 0)})
                balance[antigen] = closing
            if fridge[fac]:
                payload["fridge_working"] = "yes" if rng.random() < 0.97 else "no"
                payload["temp_alarms"] = str(rng.choices([0, 1, 2, 3], [88, 7, 3, 2])[0])
            if rng.random() < 0.92:    # some weeks are never reported
                records.append(("stock", sync_time(report_day, rng), payload))

    # Kobo's own fields, so every record looks like an API record.
    records.sort(key=lambda r: r[1])
    out = []
    for n, (form, synced_at, payload) in enumerate(records, start=1):
        instance = str(uuid.UUID(int=rng.getrandbits(128)))
        payload = dict(payload)
        payload.update({"meta/instanceID": f"uuid:{instance}", "_uuid": instance, "_id": 900000000 + n,
                        "_submission_time": synced_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")})
        out.append((form, synced_at, payload))
    return out


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def write_history(records, cutoff):
    folder = OUTPUT_DIR / "history"
    folder.mkdir(parents=True, exist_ok=True)
    counts = {}
    files = {form: open(folder / f"{form}.jsonl", "w", encoding="utf-8") for form in FORMS}
    try:
        for form, synced_at, payload in records:
            if synced_at.date() <= cutoff:
                payload = dict(payload, _xform_id_string=os.getenv(FORMS[form], form))
                files[form].write(json.dumps(payload) + "\n")
                counts[form] = counts.get(form, 0) + 1
    finally:
        for f in files.values():
            f.close()
    save_state({"history_cutoff": cutoff.isoformat(), "submitted": []})
    for form, n in counts.items():
        print(f"{form}: {n} records -> data/generated/history/{form}.jsonl")


def kobo_deployment(asset_uid):
    server, token = os.getenv("KOBO_SERVER").rstrip("/"), os.getenv("KOBO_TOKEN")
    response = requests.get(f"{server}/api/v2/assets/{asset_uid}/", params={"format": "json"},
                            headers={"Authorization": f"Token {token}"}, timeout=60)
    response.raise_for_status()
    asset = response.json()
    identifier = asset["deployment__identifier"]
    parsed = urlparse(identifier)
    return f"{parsed.scheme}://{parsed.netloc}", identifier.rstrip("/").split("/")[-1], asset.get("deployed_version_id")


def to_xml(payload, id_string, version):
    root = ET.Element("data", {"id": id_string, "version": version or ""})
    for key, value in payload.items():
        if key.startswith("_") or key == "meta/instanceID" or value is None:
            continue
        node = root
        for part in key.split("/"):
            child = node.find(part)
            node = child if child is not None else ET.SubElement(node, part)
        node.text = str(value)
    if version:
        ET.SubElement(root, "__version__").text = version
    meta = ET.SubElement(root, "meta")
    ET.SubElement(meta, "instanceID").text = payload["meta/instanceID"]
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def submit_daily(records, today, dry_run):
    state = load_state()
    if "history_cutoff" not in state:
        raise SystemExit("Run --mode history first.")
    cutoff = date.fromisoformat(state["history_cutoff"])
    done = set(state.get("submitted", []))
    due = [(f, s, p) for f, s, p in records
           if cutoff < s.date() <= today and p["meta/instanceID"] not in done]
    print(f"{len(due)} records synced between {cutoff + timedelta(days=1)} and {today}.")
    if dry_run or not due:
        for form in FORMS:
            print(f"  {form}: {sum(1 for f, _, _ in due if f == form)}")
        if due:
            print("Example:", json.dumps(due[0][2], indent=2)[:1200])
        return

    token = os.getenv("KOBO_TOKEN")
    deployments = {form: kobo_deployment(os.getenv(env)) for form, env in FORMS.items()}
    sent = 0
    try:
        for form, _, payload in due:
            kc_base, id_string, version = deployments[form]
            response = requests.post(
                f"{kc_base}/submission",
                files={"xml_submission_file": ("submission.xml", to_xml(payload, id_string, version), "text/xml")},
                headers={"Authorization": f"Token {token}", "X-OpenRosa-Version": "1.0"},
                timeout=60,
            )
            if response.status_code not in (201, 202):
                print(f"Stopped: Kobo returned {response.status_code} for a {form} record.")
                print(response.text[:500])
                break
            done.add(payload["meta/instanceID"])
            sent += 1
    finally:
        state["submitted"] = sorted(done)
        save_state(state)
    print(f"Submitted {sent} of {len(due)} records.")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["history", "daily"], required=True)
    parser.add_argument("--dry-run", action="store_true", help="daily mode: show what would be sent")
    parser.add_argument("--today", type=date.fromisoformat, default=date.today(),
                        help="override today's date (YYYY-MM-DD), for testing")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    records = generate_world(*load_reference_data())
    if args.mode == "history":
        write_history(records, args.today)
    else:
        submit_daily(records, args.today, args.dry_run)


if __name__ == "__main__":
    main()
