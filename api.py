from flask import Flask, request, jsonify
import psycopg2, os
from datetime import timezone

app = Flask(__name__)
PG_DSN = os.getenv(
    "PG_DSN",
    "host=localhost port=5432 dbname=wikidb user=postgres password=postgres connect_timeout=5",
)

@app.get("/healthz")
def healthz():
    try:
        conn = psycopg2.connect(PG_DSN); conn.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

# 取可選 wiki 清單（按最近有資料排序）
@app.get("/wikis")
def wikis():
    conn = psycopg2.connect(PG_DSN); cur = conn.cursor()
    cur.execute("""
      SELECT wiki, max(ts_minute) AS latest
      FROM gold.edits_per_min
      GROUP BY 1
      ORDER BY latest DESC
      LIMIT 50
    """)
    rows = cur.fetchall(); conn.close()
    return jsonify([r[0] for r in rows])

# 回傳每分鐘：total/human/bot
@app.get("/edits")
def edits():
    wiki = request.args.get("wiki", "enwiki")
    minutes = int(request.args.get("minutes", "180"))
    conn = psycopg2.connect(PG_DSN); cur = conn.cursor()
    cur.execute("""
      SELECT
        ts_minute,
        SUM(edits) AS total,
        SUM(CASE WHEN is_bot THEN edits ELSE 0 END) AS bot,
        SUM(CASE WHEN NOT is_bot THEN edits ELSE 0 END) AS human
      FROM gold.edits_per_min
      WHERE wiki=%s AND ts_minute >= now() - (%s || ' minutes')::interval
      GROUP BY ts_minute
      ORDER BY ts_minute
    """, (wiki, minutes))
    rows = cur.fetchall(); conn.close()
    out=[]
    for ts,total,bot,human in rows:
        ts_utc = ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append({"ts": ts_utc, "total": int(total), "human": int(human), "bot": int(bot)})
    return jsonify(out)

@app.get("/")
def index():
    return """
<!doctype html><meta charset="utf-8" />
<title>Wikimedia edits per minute</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/c3@0.7.20/c3.min.css">
<style>
  :root { --fg:#0f172a; --muted:#64748b; --card:#fff; --bg:#f8fafc; }
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Inter,Arial,sans-serif;background:var(--bg);color:var(--fg);margin:0}
  .wrap{max-width:960px;margin:32px auto;padding:0 16px}
  .card{background:var(--card);border-radius:16px;box-shadow:0 10px 25px rgba(15,23,42,.06);padding:20px}
  h1{margin:0 0 16px;font-size:26px}
  .controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px}
  .controls input,.controls select{padding:8px 10px;border:1px solid #e2e8f0;border-radius:10px}
  .btn{padding:8px 12px;border-radius:10px;border:1px solid #cbd5e1;background:#fff;cursor:pointer}
  .btnset button{margin-right:6px}
  #chart{height:360px}
  small{color:var(--muted)}
</style>
<div class="wrap">
  <div class="card">
    <h1>Wikimedia edits per minute</h1>
    <div class="controls">
      <label>Wiki:
        <select id="wiki"></select>
      </label>
      <label>Minutes:
        <input id="mins" value="1440" size="6"/>
      </label>
      <div class="btnset">
        <button class="btn" onclick="setMins(60)">1h</button>
        <button class="btn" onclick="setMins(180)">3h</button>
        <button class="btn" onclick="setMins(1440)">24h</button>
        <button class="btn" onclick="setMins(10080)">7d</button>
      </div>
      <label>Smooth (MA):
        <select id="ma"><option value="1">off</option><option value="3">3m</option><option value="5" selected>5m</option><option value="15">15m</option></select>
      </label>
      <label><input type="checkbox" id="showHuman" checked/> human</label>
      <label><input type="checkbox" id="showBot" checked/> bot</label>
      <button class="btn" onclick="draw()">Load</button>
    </div>
    <div id="chart"></div>
    <small>UTC 時間；Total = Human + Bot</small>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/d3@5"></script>
<script src="https://cdn.jsdelivr.net/npm/c3@0.7.20/c3.min.js"></script>
<script>
const Q = sel => document.querySelector(sel);

function setMins(v){ Q('#mins').value = v; draw(); }

function movingAvg(arr, w){
  if (w<=1) return arr.slice();
  const out=[]; let sum=0; const q=[];
  for (let i=0;i<arr.length;i++){
    q.push(arr[i]); sum+=arr[i];
    if (q.length>w) sum-=q.shift();
    out.push( Math.round(sum/q.length) );
  }
  return out;
}

async function loadWikis(){
  const res = await fetch('/wikis'); const list = await res.json();
  const sel = Q('#wiki'); sel.innerHTML='';
  for(const w of list){ const opt=document.createElement('option'); opt.value=opt.textContent=w; sel.appendChild(opt); }
  if(!list.includes('enwiki') && list.length>0) sel.value=list[0]; else sel.value='enwiki';
}

async function draw(){
  const wiki = Q('#wiki').value || 'enwiki';
  const mins = Q('#mins').value || '1440';
  const maWin = parseInt(Q('#ma').value,10);
  const showHuman = Q('#showHuman').checked;
  const showBot   = Q('#showBot').checked;

  const res = await fetch(`/edits?wiki=${wiki}&minutes=${mins}`);
  const data = await res.json();

  if(!Array.isArray(data) || data.length===0){
    d3.select('#chart').html('<p>No data.</p>');
    return;
  }
  const ts = ['x', ...data.map(d=>d.ts)];
  const total = ['total', ...movingAvg(data.map(d=>d.total), maWin)];
  const human = ['human', ...movingAvg(data.map(d=>d.human), maWin)];
  const bot   = ['bot',   ...movingAvg(data.map(d=>d.bot),   maWin)];

  const cols = [ts, total];
  if (showHuman) cols.push(human);
  if (showBot) cols.push(bot);

  c3.generate({
    bindto: '#chart',
    data: { x:'x', columns: cols, types:{ total:'area-spline', human:'spline', bot:'spline' } },
    color: { pattern: ['#2563eb','#16a34a','#ef4444'] },  // total / human / bot
    point: { show:false },
    legend: { position:'inset' },
    axis: {
      x: { type:'timeseries', tick:{ format:'%m-%d %H:%M', culling:{max:8} } },
      y: { min:0, padding:{bottom:0} }
    },
    grid: { y: { show:true } },
    padding: { top:10, right:10, bottom:0, left:45 },
    transition: { duration: 250 }
  });
}

loadWikis().then(draw);
</script>
"""
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
