#!/usr/bin/env python3
"""
Novel Harness reality check: model a 500-chapter Chinese web novel story graph
at realistic scale, then run the 5 query classes the spec (section 7) requires
-- in plain SQL on SQLite (a deliberately PESSIMISTIC stand-in for PostgreSQL).

If SQLite clears the latency bar, Postgres clears it with margin.
"""
import sqlite3
import random
import time
import statistics
import os

random.seed(42)
DB = os.environ.get("NH_BENCH_DB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "novel.db")
if os.path.exists(DB):
    os.remove(DB)

con = sqlite3.connect(DB)
cur = con.cursor()
cur.executescript("""
PRAGMA journal_mode=WAL;
CREATE TABLE node (
  id INTEGER PRIMARY KEY,
  biz_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  name TEXT
);
CREATE TABLE edge (
  id INTEGER PRIMARY KEY,
  project_id TEXT NOT NULL,
  src INTEGER NOT NULL,
  dst INTEGER NOT NULL,
  type TEXT NOT NULL,
  valid_from_chapter INTEGER NOT NULL,
  valid_to_chapter INTEGER,          -- NULL = still valid
  canon_version INTEGER NOT NULL,
  status TEXT NOT NULL,
  information_scope TEXT NOT NULL,
  confidence REAL,
  evidence_id INTEGER
);
""")

CH = 500
PROJ = "novel-001"
nodes = []
def add(kind, n, prefix):
    out = []
    for i in range(n):
        out.append((f"{prefix}:{i}", PROJ, kind, f"{prefix}-{i}"))
    start = len(nodes)
    nodes.extend(out)
    return list(range(start, start + n))

# ---- realistic counts for a 500-chapter / ~1.5M-char web novel ----
chapters  = add("Chapter", CH, "chapter")
scenes    = add("Scene", CH*4, "scene")          # 4 scenes/chapter
chars     = add("Character", 200, "character")   # ~30 major + long tail
locations = add("Location", 150, "location")
factions  = add("Faction", 30, "faction")
objects   = add("Object", 200, "object")
events    = add("Event", CH*10, "event")         # 10 events/chapter
facts     = add("Fact", CH*30, "fact")           # 30 facts/chapter
states    = add("State", 12000, "state")
relstates = add("RelationshipState", 6000, "relstate")
foreshadow= add("Foreshadow", 200, "foreshadow")
secrets   = add("Secret", 150, "secret")
knowitems = add("KnowledgeItem", 8000, "knowitem")
evidence  = add("EvidenceRef", 30000, "evidence")

cur.executemany("INSERT INTO node(biz_id,project_id,kind,name) VALUES (?,?,?,?)", nodes)

edges = []
def E(src, dst, typ, vf, vt, scope="CANON", status="ACTIVE"):
    edges.append((PROJ, src+1, dst+1, typ, vf, vt, vf, status, scope, 0.9, random.choice(evidence)+1))

majors = chars[:30]

# APPEARS_IN / PARTICIPATES_IN / OCCURS_AT
for si, s in enumerate(scenes):
    ch = si // 4
    E(s, chapters[ch], "APPEARS_IN", ch, None)
    for c in random.sample(chars, random.randint(2, 5)):
        E(c, s, "APPEARS_IN", ch, None)
    E(s, random.choice(locations), "OCCURS_AT", ch, None)

for ei, e in enumerate(events):
    ch = ei // 10
    E(e, chapters[ch], "APPEARS_IN", ch, None)
    for c in random.sample(chars, random.randint(1, 4)):
        E(c, e, "PARTICIPATES_IN", ch, None)
    E(e, random.choice(locations), "OCCURS_AT", ch, None)

# CAUSES: genuine variable-depth DAG, forward in time only
for ei, e in enumerate(events):
    ch = ei // 10
    for _ in range(random.randint(0, 2)):
        tgt = random.randint(ei+1, min(ei+120, len(events)-1)) if ei+1 < len(events) else None
        if tgt: E(e, events[tgt], "CAUSES", tgt // 10, None)

# HAS_STATE: temporal versioning -- each major char's attrs change over time
attrs = ["location", "cultivation", "identity", "emotion", "health"]
si = 0
for c in chars:
    n_changes = 40 if c in majors else 4
    for a in attrs:
        pts = sorted(random.sample(range(1, CH), min(n_changes, CH-1)))
        for k, start in enumerate(pts):
            end = pts[k+1] if k+1 < len(pts) else None
            if si < len(states):
                E(c, states[si], "HAS_STATE", start, end)
                si += 1

# RELATED_TO via RelationshipState: ~1200 meaningful pairs, multiple stages each
pairs = set()
while len(pairs) < 1200:
    a, b = random.sample(chars, 2)
    pairs.add((min(a,b), max(a,b)))
ri = 0
for (a, b) in pairs:
    stages = sorted(random.sample(range(1, CH), random.randint(1, 6)))
    for k, start in enumerate(stages):
        end = stages[k+1] if k+1 < len(stages) else None
        if ri < len(relstates):
            E(a, relstates[ri], "RELATED_TO", start, end)
            E(relstates[ri], b, "RELATED_TO", start, end)
            ri += 1

# Knowledge boundary: KNOWS / PARTIALLY_KNOWS / BELIEVES / DOES_NOT_KNOW
for k in knowitems:
    c = random.choice(chars)
    tgt = random.choice(facts + secrets)
    typ = random.choice(["KNOWS","PARTIALLY_KNOWS","BELIEVES","DOES_NOT_KNOW"])
    start = random.randint(1, CH-1)
    E(c, k, typ, start, None)
    E(k, tgt, "SUPPORTED_BY", start, None)

# Foreshadow: planted -> advanced* -> resolved (star, not chain)
for f in foreshadow:
    p = random.randint(1, CH-100)
    E(f, chapters[p], "PLANTED_IN", p, None)
    for adv in sorted(random.sample(range(p+1, min(p+90, CH)), random.randint(0, 4))):
        E(f, chapters[adv], "ADVANCED_IN", adv, None)
    if random.random() < 0.7:
        r = random.randint(p+10, CH-1)
        E(f, chapters[r], "RESOLVED_IN", r, None)

# Facts + evidence
for fi, f in enumerate(facts):
    ch = fi // 30
    E(f, chapters[ch], "APPEARS_IN", ch, None)
    E(f, random.choice(evidence), "SUPPORTED_BY", ch, None)
    if random.random() < 0.02:
        E(f, random.choice(facts), "CONTRADICTS", ch, None)

cur.executemany("""INSERT INTO edge(project_id,src,dst,type,valid_from_chapter,valid_to_chapter,
                   canon_version,status,information_scope,confidence,evidence_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""", edges)

cur.executescript("""
CREATE INDEX ix_edge_src ON edge(project_id, src, type, valid_from_chapter, valid_to_chapter);
CREATE INDEX ix_edge_dst ON edge(project_id, dst, type, valid_from_chapter, valid_to_chapter);
CREATE INDEX ix_node_biz ON node(project_id, biz_id);
CREATE INDEX ix_node_kind ON node(project_id, kind);
ANALYZE;
""")
con.commit()

n_nodes = cur.execute("SELECT count(*) FROM node").fetchone()[0]
n_edges = cur.execute("SELECT count(*) FROM edge").fetchone()[0]
size_mb = os.path.getsize(DB) / 1e6
print("=== SCALE: 500-chapter novel ===")
print(f"nodes = {n_nodes:,}")
print(f"edges = {n_edges:,}")
print(f"on-disk = {size_mb:.1f} MB")
for k, in cur.execute("SELECT DISTINCT kind FROM node"):
    c = cur.execute("SELECT count(*) FROM node WHERE kind=?", (k,)).fetchone()[0]
    print(f"   {k:22s} {c:>7,}")
print()

def bench(label, sql, params, runs=20):
    cur.execute(sql, params); cur.fetchall()          # warm
    ts = []
    for _ in range(runs):
        t = time.perf_counter()
        cur.execute(sql, params); rows = cur.fetchall()
        ts.append((time.perf_counter()-t)*1000)
    print(f"{label:46s} p50={statistics.median(ts):7.2f}ms  p95={sorted(ts)[int(len(ts)*.95)-1]:7.2f}ms  rows={len(rows)}")

AT = 380  # "state as of chapter 380"

# ---- Q1: 某章时状态 ----
bench("Q1 state-as-of-chapter (one character)", """
SELECT n.biz_id, e.valid_from_chapter, e.valid_to_chapter
FROM edge e JOIN node n ON n.id = e.dst
WHERE e.project_id=? AND e.src=? AND e.type='HAS_STATE'
  AND e.valid_from_chapter <= ?
  AND (e.valid_to_chapter IS NULL OR e.valid_to_chapter > ?)
  AND e.status='ACTIVE' AND e.information_scope='CANON'
""", (PROJ, majors[0]+1, AT, AT))

# Q1b: ALL characters' state at chapter -- the ChapterBrief build
bench("Q1b state-as-of-chapter (ALL 200 chars)", """
SELECT e.src, n.biz_id
FROM edge e JOIN node n ON n.id = e.dst
WHERE e.project_id=? AND e.type='HAS_STATE'
  AND e.valid_from_chapter <= ?
  AND (e.valid_to_chapter IS NULL OR e.valid_to_chapter > ?)
  AND e.status='ACTIVE'
""", (PROJ, AT, AT))

# ---- Q2: 1-3 跳时间态子图 ----
subgraph_sql = """
WITH RECURSIVE reach(id, depth) AS (
  SELECT ?, 0
  UNION
  SELECT CASE WHEN e.src = r.id THEN e.dst ELSE e.src END, r.depth + 1
  FROM reach r
  JOIN edge e ON (e.src = r.id OR e.dst = r.id)
  WHERE r.depth < ?
    AND e.project_id = ?
    AND e.valid_from_chapter <= ?
    AND (e.valid_to_chapter IS NULL OR e.valid_to_chapter > ?)
    AND e.status = 'ACTIVE'
    AND e.type IN ('RELATED_TO','HAS_STATE','PARTICIPATES_IN','KNOWS','OWNS','MEMBER_OF','LOCATED_AT')
)
SELECT r.depth, n.kind, n.biz_id FROM reach r JOIN node n ON n.id = r.id
"""
for d in (1, 2, 3):
    bench(f"Q2 {d}-hop temporal subgraph @ch{AT}", subgraph_sql, (majors[0]+1, d, PROJ, AT, AT), runs=10)

# ---- Q3: 人物知识边界 ----
bench("Q3 knowledge boundary (one char @ch)", """
SELECT k.type, n2.biz_id AS fact
FROM edge k
JOIN edge s ON s.src = k.dst AND s.type='SUPPORTED_BY'
JOIN node n2 ON n2.id = s.dst
WHERE k.project_id=? AND k.src=?
  AND k.type IN ('KNOWS','PARTIALLY_KNOWS','BELIEVES','DOES_NOT_KNOW')
  AND k.valid_from_chapter <= ?
  AND (k.valid_to_chapter IS NULL OR k.valid_to_chapter > ?)
""", (PROJ, majors[0]+1, AT, AT))

# Q3b: the actual hot path -- "may character X mention fact F at chapter N?"
bench("Q3b leak check: can char X mention fact F?", """
SELECT k.type FROM edge k
JOIN edge s ON s.src = k.dst AND s.type='SUPPORTED_BY' AND s.dst = ?
WHERE k.project_id=? AND k.src=?
  AND k.type IN ('KNOWS','PARTIALLY_KNOWS','BELIEVES','DOES_NOT_KNOW')
  AND k.valid_from_chapter <= ? AND (k.valid_to_chapter IS NULL OR k.valid_to_chapter > ?)
""", (facts[100]+1, PROJ, majors[0]+1, AT, AT))

# ---- Q4: 伏笔链 ----
bench("Q4 foreshadow plant/advance/resolve chain", """
SELECT e.type, n.biz_id, e.valid_from_chapter
FROM edge e JOIN node n ON n.id=e.dst
WHERE e.project_id=? AND e.src=? AND e.type IN ('PLANTED_IN','ADVANCED_IN','RESOLVED_IN')
ORDER BY e.valid_from_chapter
""", (PROJ, foreshadow[3]+1,))

# Q4b: unresolved foreshadows as of chapter -- the real product question
bench("Q4b open foreshadows @ch (whole book)", """
SELECT p.src, min(p.valid_from_chapter)
FROM edge p
WHERE p.project_id=? AND p.type='PLANTED_IN' AND p.valid_from_chapter <= ?
  AND NOT EXISTS (SELECT 1 FROM edge r WHERE r.src=p.src AND r.type='RESOLVED_IN' AND r.valid_from_chapter <= ?)
GROUP BY p.src
""", (PROJ, AT, AT))

# ---- Q5: 因果链 (genuine variable-depth DAG traversal) ----
for d in (3, 5, 8):
    bench(f"Q5 causal chain depth<={d}", """
    WITH RECURSIVE causal(id, depth) AS (
      SELECT ?, 0
      UNION
      SELECT e.dst, c.depth+1 FROM causal c
      JOIN edge e ON e.src = c.id AND e.type='CAUSES'
      WHERE c.depth < ? AND e.project_id=? AND e.status='ACTIVE'
    )
    SELECT c.depth, n.biz_id FROM causal c JOIN node n ON n.id=c.id
    """, (events[10]+1, d, PROJ), runs=10)

con.close()
