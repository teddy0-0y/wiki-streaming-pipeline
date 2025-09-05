import os, json, gzip, datetime as dt
from collections import defaultdict
import boto3, psycopg2

BUCKET   = os.getenv("BUCKET", "wiki-pipeline")
REGION   = os.getenv("AWS_REGION", "ap-southeast-2")
PROFILE  = os.getenv("AWS_PROFILE", "wiki")
PG_DSN   = os.getenv("PG_DSN", "host=localhost dbname=wikidb user=postgres password=postgres")
HOURS    = int(os.getenv("HOURS", "6"))  # 抓近幾小時的檔

session = boto3.Session(profile_name=PROFILE)
s3 = session.client("s3", region_name=REGION)
conn = psycopg2.connect(PG_DSN); cur = conn.cursor()

UPSERT_SQL = """
INSERT INTO gold.edits_per_min(ts_minute, wiki, is_bot, edits, bytes_change)
VALUES (%s,%s,%s,%s,%s)
ON CONFLICT (ts_minute, wiki, is_bot)
DO UPDATE SET edits = gold.edits_per_min.edits + EXCLUDED.edits,
              bytes_change = gold.edits_per_min.bytes_change + EXCLUDED.bytes_change;
"""

def list_recent_keys():
    now = dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    for i in range(HOURS):
        t = now - dt.timedelta(hours=i)
        prefix = f"bronze/yyyy={t:%Y}/mm={t:%m}/dd={t:%d}/hh={t:%H}/"
        token = None
        while True:
            kw = dict(Bucket=BUCKET, Prefix=prefix)
            if token: kw["ContinuationToken"] = token
            r = s3.list_objects_v2(**kw)
            for it in r.get("Contents", []):
                yield it["Key"]
            token = r.get("NextContinuationToken")
            if not token: break

def try_mark_processing(key:str)->bool:
    cur.execute("INSERT INTO gold.processed_keys(s3_key) VALUES (%s) ON CONFLICT DO NOTHING", (key,))
    return cur.rowcount == 1  # True 表示第一次處理

def process_key(key):
    # idempotency：如果處理過就跳過
    if not try_mark_processing(key):
        print(f"[SKIP] {key} already processed"); conn.commit(); return
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    lines = gzip.decompress(obj["Body"].read()).decode().splitlines()

    agg = defaultdict(lambda: [0,0])  # (edits, bytes_change)
    seen = set()
    for ln in lines:
        j = json.loads(ln)
        if j.get("type") != "edit" or "timestamp" not in j: continue
        did = j.get("meta",{}).get("id")
        if did:
            if did in seen: continue
            seen.add(did)
        ts = dt.datetime.utcfromtimestamp(int(j["timestamp"])).replace(second=0, microsecond=0, tzinfo=dt.timezone.utc)
        wiki = j.get("wiki","unknown")
        bot  = bool(j.get("bot", False))
        old = int(j.get("length",{}).get("old",0)); new = int(j.get("length",{}).get("new",0))
        agg[(ts,wiki,bot)][0] += 1
        agg[(ts,wiki,bot)][1] += (new - old)

    for (ts,wiki,bot),(ed,chg) in agg.items():
        cur.execute(UPSERT_SQL, (ts,wiki,bot,ed,chg))
    conn.commit()
    print(f"[OK] {key} rows_in={len(lines)} mins={len(agg)}")

if __name__ == "__main__":
    cnt=0
    for k in list_recent_keys():
        process_key(k); cnt+=1
    print(f"[DONE] processed keys: {cnt}")
