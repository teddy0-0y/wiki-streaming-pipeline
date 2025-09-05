import os, sys, json, time, gzip, uuid, datetime as dt
import requests, boto3
from sseclient import SSEClient

# ---- 環境設定 ----
BUCKET = os.getenv("BUCKET")
REGION = os.getenv("AWS_REGION", "ap-southeast-2")
PROFILE = os.getenv("AWS_PROFILE", "wiki")
ENDPOINT = os.getenv("AWS_ENDPOINT_URL")  # MinIO 用
FLUSH_MIN_LINES = int(os.getenv("FLUSH_MIN_LINES", "10"))
FLUSH_MAX_SEC = int(os.getenv("FLUSH_MAX_SEC", "10"))
FILTER_WIKI = os.getenv("FILTER_WIKI")  # ex: enwiki

if not BUCKET:
    sys.exit("Missing env BUCKET (ex: export BUCKET=wiki-pipeline)")

session = boto3.Session(profile_name=PROFILE) if PROFILE else boto3.Session()
s3 = session.client("s3", region_name=REGION, endpoint_url=ENDPOINT)

STREAM = "https://stream.wikimedia.org/v2/stream/recentchange"
UA = {"User-Agent": "wikichanges-demo/1.0 (+ingest)"}

def event_time(msg) -> dt.datetime:
    # 先用 'timestamp' (epoch 秒)，若沒有再用 meta.dt，最後 fallback 現在
    try:
        return dt.datetime.fromtimestamp(int(msg["timestamp"]), tz=dt.timezone.utc)
    except Exception:
        try:
            return dt.datetime.fromisoformat(msg["meta"]["dt"].replace("Z","+00:00"))
        except Exception:
            return dt.datetime.now(tz=dt.timezone.utc)

def rotate_key(ev_dt: dt.datetime) -> str:
    return f"bronze/yyyy={ev_dt:%Y}/mm={ev_dt:%m}/dd={ev_dt:%d}/hh={ev_dt:%H}/part-{uuid.uuid4().hex}.ndjson.gz"

def flush(key: str, lines: list[bytes]) -> None:
    if not key or not lines:
        return
    body = gzip.compress(b"".join(lines))
    s3.put_object(
        Bucket=BUCKET, Key=key, Body=body,
        ContentEncoding="gzip", ContentType="application/x-ndjson"
    )
    print(f"[FLUSH] s3://{BUCKET}/{key} rows={len(lines)}", flush=True)

def stream_once():
    print("[START] connecting to Wikimedia EventStreams...", flush=True)
    resp = requests.get(
        STREAM, stream=True, timeout=(10, 60), headers=UA
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from EventStreams")
    print("[OK] connected. streaming events...", flush=True)

    client = SSEClient(resp)
    buf: list[bytes] = []
    key = None
    started = time.time()
    count = 0

    for ev in client.events():
        if ev.event != "message":
            continue
        try:
            m = json.loads(ev.data)
        except Exception as e:
            print(f"[WARN] bad json: {e}", flush=True)
            continue

        if m.get("type") != "edit" or "timestamp" not in m:
            continue
        if FILTER_WIKI and m.get("wiki") != FILTER_WIKI:
            continue

        et = event_time(m)
        if key is None:
            key = rotate_key(et)
        buf.append((json.dumps(m, ensure_ascii=False) + "\n").encode("utf-8"))
        count += 1

        if count % 5 == 0:
            print(f"[EVT] received={count}", flush=True)

        # 快速 flush 條件：行數或經過秒數
        if len(buf) >= FLUSH_MIN_LINES or time.time() - started > FLUSH_MAX_SEC:
            flush(key, buf)
            buf, key, started = [], rotate_key(et), time.time()

def main():
    # 簡單自動重連
    backoff = 2
    while True:
        try:
            stream_once()
        except KeyboardInterrupt:
            print("[STOP] interrupted by user.", flush=True)
            break
        except Exception as e:
            print(f"[ERROR] {e}; reconnect in {backoff}s...", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)  # 最多退避到 60s

if __name__ == "__main__":
    main()