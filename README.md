# Community Health MEL Pipeline

An end-to-end data pipeline for a year-round routine immunisation programme, that automatically collect community health worker visit data, track every child's vaccination schedule, and turn gaps, risks and opportunities into an actionable follow-up list for programme teams, while tracking progress toward 90% coverage and dropout below 10%.

**All data is synthetic.** No real children, caregivers or staff are included.

## Stack
- **Sources:** KoboToolbox (3 forms), Airtable (5 reference tables)
- **Ingestion:** Python (incremental loads, upserts, HMAC-SHA256 hashing of personal data)
- **Warehouse:** PostgreSQL (local in Docker; Cloud SQL on GCP)
- **Transformation and data quality:** dbt (bronze, silver, gold)
- **Orchestration:** Cloud Run Jobs triggered by Cloud Scheduler
- **Reporting:** Power BI and Power Automate
- **CI:** GitHub Actions

## Status
Work in progress. See `docs/` for the project brief.

## Repository Structure
- `docs/` System architecture, data dictionaries, and setup guides
- `src/` Python ingestion scripts for HDX API & mobile survey data
- `dags/` Airflow DAGs for scheduled pipeline execution
- `dbt/` Data transformations and quality testing models
