import sqlite3
import time
import os
import sys
DB = os.environ.get("NH_BENCH_DB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "novel.db")
if not os.path.exists(DB):
    sys.exit(f"{DB} not found -- run `python scale_bench.py` first to build the 500-chapter fixture.")
con=sqlite3.connect(DB); cur=con.cursor()
# a real major character (character:0 == first of the 30 majors)
cid=cur.execute("SELECT id FROM node WHERE biz_id='character:0'").fetchone()[0]
print(f"start node = character:0 (id={cid})\n")

def walk(prune, depth=3):
    temporal = "AND e.valid_from_chapter <= 380 AND (e.valid_to_chapter IS NULL OR e.valid_to_chapter > 380)" if prune else ""
    sql=f"""
    WITH RECURSIVE reach(id,depth) AS (
      SELECT {cid},0
      UNION
      SELECT CASE WHEN e.src=r.id THEN e.dst ELSE e.src END, r.depth+1
      FROM reach r JOIN edge e ON (e.src=r.id OR e.dst=r.id)
      WHERE r.depth<{depth} AND e.project_id='novel-001' {temporal}
        AND e.type IN ('RELATED_TO','HAS_STATE','PARTICIPATES_IN','KNOWS','OWNS','MEMBER_OF')
    ) SELECT count(*) FROM reach"""
    cur.execute(sql).fetchone()
    t=time.perf_counter(); n=cur.execute(sql).fetchone()[0]; ms=(time.perf_counter()-t)*1000
    return n,ms

for d in (1,2,3):
    n1,m1=walk(True,d); n2,m2=walk(False,d)
    print(f"depth {d}:  as-of-ch380 -> {n1:>6,} nodes {m1:8.2f}ms   |   ALL history -> {n2:>6,} nodes {m2:8.2f}ms   | history is {n2/max(n1,1):.1f}x wider, {m2/max(m1,0.01):.1f}x slower")
con.close()
