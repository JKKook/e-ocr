"""질의 trace — 단계별 ms·토큰·비용·가드 결과·피드백을 SQLite(data/traces.db)에 남긴다."""
import json
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "traces.db"
STAGES = ("condense_ms", "embed_ms", "search_ms", "rerank_ms", "gen_ms", "total_ms")


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""create table if not exists trace (
        id integer primary key, ts real, q text, standalone text, llm text, collection text,
        confidence text, top_score real, issues text, retried integer, answer text, hits text,
        stages text, in_tok integer, out_tok integer, usd real, feedback integer)""")
    return c


def record(t: dict) -> int:
    with db() as c:
        cur = c.execute(
            "insert into trace (ts,q,standalone,llm,collection,confidence,top_score,issues,retried,answer,hits,stages,in_tok,out_tok,usd)"
            " values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), t["q"], t["standalone"], t["llm"], t["collection"], t["confidence"], t["top_score"],
             json.dumps(t["issues"], ensure_ascii=False), int(t["retried"]), t["answer"],
             json.dumps(t["hit_ids"]), json.dumps(t["stages"]), t.get("in_tok"), t.get("out_tok"), t.get("usd", 0.0)))
        return cur.lastrowid


def feedback(tid: int, value: int) -> None:
    with db() as c:
        c.execute("update trace set feedback=? where id=?", (value, tid))


def rows(limit: int = 100) -> list[dict]:
    with db() as c:
        out = [dict(r) for r in c.execute("select * from trace order by id desc limit ?", (limit,))]
    for r in out:
        r["issues"], r["hits"], r["stages"] = json.loads(r["issues"]), json.loads(r["hits"]), json.loads(r["stages"])
    return out


def stats() -> dict:
    rs = rows(10_000)
    pct = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))] if xs else None
    by = {s: [r["stages"][s] for r in rs if s in r["stages"]] for s in STAGES}
    fb = [r["feedback"] for r in rs if r["feedback"] is not None]
    return {
        "n": len(rs),
        "stages": {s: {"p50": pct(v, .5), "p95": pct(v, .95), "n": len(v)} for s, v in by.items()},
        "refused": sum(r["confidence"] == "none" for r in rs), "low": sum(r["confidence"] == "low" for r in rs),
        "flagged": sum(bool(r["issues"]) for r in rs), "retried": sum(r["retried"] for r in rs),
        "usd": round(sum(r["usd"] or 0 for r in rs), 4),
        "feedback": {"up": fb.count(1), "down": fb.count(-1)},
    }
