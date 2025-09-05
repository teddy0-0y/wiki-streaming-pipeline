# 🧠 Wikimedia Streaming ETL — Real-time Wikipedia Edit Tracker

A side project built by **Yen-Chu Chen** to monitor and visualize Wikipedia edit activity in real time across languages.  
This repo streams edit events from Wikimedia, stores them in S3, transforms and aggregates them using Python, and visualizes the trends with a lightweight dashboard.

---

## 🗺️ Overview

- **Source**: Wikimedia EventStreams (`recentchange`)
- **Bronze Layer**: Raw JSON logs written to S3
- **Gold Layer**: Aggregated edit counts per minute
- **Serving**: Flask API with C3.js-based dashboard
- **Option**: S3-only mode without database

---

## 🏗️ Architecture

```
Wikimedia EventStreams (SSE)
        │
        ▼
ingest_wiki_to_s3.py
  → s3://<bucket>/bronze/yyyy=YYYY/mm=MM/dd=DD/hh=HH/part-*.ndjson.gz
        │
        ▼
transform_to_minute_agg.py   (idempotent)
  → Postgres/TimescaleDB:
    - gold.edits_per_min
    - gold.processed_keys
        │
        ▼
api.py (Flask)
  → /wikis /edits /healthz + chart on /
```

---

## 📁 Project Structure

```
.
├── ingest_wiki_to_s3.py          ← Stream edits into hourly S3 partitions
├── transform_to_minute_agg.py    ← Transform raw to aggregated metrics in DB
├── transform_to_gold_s3.py       ← Alternative: write gold back to S3
├── api.py                        ← Flask API + chart UI
├── api_s3.py                     ← Reads from S3 instead of DB
├── backfill_recentchanges.py     ← Optional: fetch historical events
├── .env.example                  ← Environment variables template
└── README.md
```

---

## ⚙️ Setup

- Python 3.11+
- AWS CLI configured (with profile `wiki`)
- Docker (for TimescaleDB)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## 🐘 Start TimescaleDB via Docker

```bash
docker run -d --name tsdb -p 5432:5432 \
  --platform linux/amd64 \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=wikidb \
  -v $HOME/tsdb-data:/var/lib/postgresql/data \
  timescale/timescaledb:latest-pg16
```

---

## 🚀 Ingest: Wikimedia → S3

```bash
export BUCKET=your-bucket
export AWS_REGION=ap-southeast-2
export AWS_PROFILE=wiki
export FILTER_WIKI=enwiki
export FLUSH_MIN_LINES=20000
export FLUSH_MAX_SEC=300

python ingest_wiki_to_s3.py
```

---

## 🔁 Transform: Raw S3 → Aggregated Metrics

```bash
export PG_DSN="host=127.0.0.1 port=5432 dbname=wikidb user=postgres password=postgres connect_timeout=5"
export HOURS=1440

python transform_to_minute_agg.py
```

---

## 🖥️ Launch Flask Dashboard

```bash
python api.py
```

Visit: `http://127.0.0.1:5000/`

---

## 🪣 S3-only Mode (No DB)

```bash
export HOURS=1440
python transform_to_gold_s3.py

export BUCKET=your-bucket
python api_s3.py
```

---

## 🧱 Postgres Schema

```sql
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS gold.edits_per_min (
  ts_minute     timestamptz NOT NULL,
  wiki          text        NOT NULL,
  is_bot        boolean     NOT NULL,
  edits         integer     NOT NULL,
  bytes_change  integer     NOT NULL,
  PRIMARY KEY (ts_minute, wiki, is_bot)
);

CREATE TABLE IF NOT EXISTS gold.processed_keys (
  s3_key        text PRIMARY KEY,
  processed_at  timestamptz DEFAULT now()
);
```

---

## 🌱 Environment Variables

| Variable          | Description                              | Example                      |
|------------------|------------------------------------------|------------------------------|
| `BUCKET`         | Your S3 bucket name                      | `wiki-pipeline`              |
| `AWS_REGION`     | AWS region                               | `ap-southeast-2`             |
| `AWS_PROFILE`    | AWS CLI profile                          | `wiki`                       |
| `FILTER_WIKI`    | Filter edits by project (e.g. `enwiki`) | `enwiki`                     |
| `FLUSH_MIN_LINES`| Min lines before flushing to S3         | `20000`                      |
| `FLUSH_MAX_SEC`  | Max seconds before flushing to S3       | `300`                        |
| `PG_DSN`         | Postgres DSN string                      | `host=... user=...`          |
| `HOURS`          | Backfill time range in hours             | `1440`                       |

---

## 💡 Cost Control Tips

- Enable [S3 lifecycle rules]:
  - **bronze**: delete after 7 days
  - **gold**: move to Intelligent-Tiering after 30 days
- Use `FILTER_WIKI=enwiki` to reduce volume
- Tune buffer with `FLUSH_MIN_LINES` / `FLUSH_MAX_SEC`

---

## 🧪 Manual Restart Flow (1 Month Later)

```bash
docker start tsdb

nohup python -u ingest_wiki_to_s3.py >> ingest.log 2>&1 & echo $! > ingest.pid

export HOURS=1440
python transform_to_minute_agg.py

python api.py
```

---

## 📝 License

MIT License © 2025 Yen-Chu Chen

---

## 🙏 Credits

- Wikimedia EventStreams  
- TimescaleDB  
- Flask  
- C3.js Chart Library
