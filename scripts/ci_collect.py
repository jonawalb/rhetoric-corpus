"""Time-boxed collector run for the nightly CI job (the GitHub Actions counterpart of scripts/resume_all.sh).

Runs the same collector commands as resume_all.sh, all sources in parallel (each host keeps its own polite delay in
collectors/lib.py; nothing here changes robots, delays or the User-Agent). Each source is a CHAIN of commands run one
after another, so no two processes ever write the same file:

  * new-items pass first: RSS / recent / sitemap passes run before that source's backfill (e.g. tr_aa recent, then
    tr_aa backfill), and --follow collectors take their newest items first and then backfill;
  * every command gets min(its own budget, time left in the global budget); at its deadline it is sent SIGTERM
    (collectors/lib.py turns that into a clean exit outside write_docs/State.save) and SIGKILL after --grace seconds;
  * docs files are repaired afterwards (a line cut by a SIGKILL is truncated), so the next append stays valid JSONL.

Output stays private: collector stdout/stderr go to logs/ci/<date>/<chain>.log (synced to the private store, not to
the public Actions log); stdout gets only counts: docs added per source file, exit status, HTTP-error counts.
Writes logs/ci/<date>/summary.json and appends a Markdown table to $GITHUB_STEP_SUMMARY when set.

Usage: uv run python scripts/ci_collect.py [--budget 13500] [--only ru_,tw_] [--skip name,...] [--list]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
FOLLOW = None  # budget marker: run until the global deadline


@dataclass(frozen=True)
class Step:
    args: Tuple[str, ...]
    budget: Optional[int] = FOLLOW  # seconds; None = until the global deadline


@dataclass(frozen=True)
class Chain:
    name: str
    steps: Tuple[Step, ...] = field(default_factory=tuple)


def chain(name: str, *steps) -> Chain:
    return Chain(name, tuple(s if isinstance(s, Step) else Step(tuple(s.split())) for s in steps))


NEW = 45 * 60  # budget of a one-pass new-items step that precedes a backfill in the same chain
# Same commands as scripts/resume_all.sh (keep in step). The 30-minute retry loops there (pk_ispr, tw_mac, sy_sana
# archive) are a single run here; the nightly schedule is the retry.
CHAINS: List[Chain] = [
    chain("by_mfa_en", "by_mfa.py --lang en"),
    chain("by_mfa_ru", "by_mfa.py --lang ru"),
    chain("by_president_en", "by_president.py --lang en"),
    chain("by_president_ru", "by_president.py --lang ru"),
    chain("in_mea", "in_mea.py"),
    chain("kp_rodong_en", "kp_rodong_en.py"),
    chain("kp_kcna_en", "kp_kcna_en.py"),
    chain("pk_mofa", "pk_mofa.py"),
    chain("pk_ispr", "pk_ispr.py"),
    chain("ru_kremlin", "ru_kremlin.py --start earliest --events"),
    chain("ru_kremlin_en", "ru_kremlin.py --lang en --start earliest --events"),
    chain("ru_mid", "ru_mid.py --start earliest"),
    chain("ru_mil", "ru_mil.py --start earliest"),
    chain("ru_scrf", "ru_scrf.py --start earliest"),
    chain("ru_ria_ru", "ru_statemedia.py ria_ru --follow --full --start earliest"),
    chain("ru_rt_com", "ru_statemedia.py rt_com --follow --full --start earliest"),
    chain("ru_rt_ru", "ru_statemedia.py rt_ru --follow --full --start earliest"),
    chain("ru_tass_com", "ru_statemedia.py tass_com --follow --full --start earliest"),
    chain("ru_sputnik_en", "ru_statemedia.py sputnik_en --follow --full --start earliest"),
    chain("ru_tass_ru", Step(("ru_tass_ru.py", "rss"), NEW), "ru_tass_ru.py backfill --start earliest"),
    chain("ru_telegram", "ru_telegram.py --follow"),
    chain("us_state", "us_state_briefings.py"),
    chain("us_whitehouse_biden", "us_whitehouse.py --site biden"),
    chain("us_whitehouse", "us_whitehouse.py --site current"),
    chain("tw_ey_en", "tw_ey.py --lang en"),
    chain("tw_ey_zh", "tw_ey.py --lang zh"),
    chain("tw_mofa_en", "tw_mofa.py --lang en"),
    chain("tw_mofa_zh", "tw_mofa.py --lang zh"),
    chain("tw_focustaiwan", "tw_media.py focustaiwan --follow"),
    chain("tw_cna", "tw_media.py cna --follow"),
    chain("tw_taipeitimes", "tw_media.py taipeitimes --follow"),
    chain("tw_mac_zh", "tw_mac.py --lang zh"),
    chain("tr_mfa_en", "tr_mfa.py --lang en"),
    chain("tr_mfa_tr", "tr_mfa.py --lang tr"),
    chain("tr_tccb_en", "tr_tccb.py --lang en"),
    chain("tr_tccb_tr", "tr_tccb.py --lang tr"),
    chain("tr_aa_en", Step(("tr_aa.py", "--lang", "en", "recent"), NEW), "tr_aa.py --lang en backfill"),
    chain("tr_aa_tr", Step(("tr_aa.py", "--lang", "tr", "recent"), NEW), "tr_aa.py --lang tr backfill"),
    chain("ir_president", "ir_president.py --follow"),
    chain("ir_khamenei_en", "ir_khamenei.py --lang en"),
    chain("ir_presstv", "ir_presstv.py --follow"),
    chain("ir_khamenei_fa", "ir_khamenei.py --lang fa"),
    chain("ir_leader", "ir_leader.py --follow"),
    chain("ir_media_iribnews", "ir_media.py iribnews --follow"),
    chain("ir_media_yjc", "ir_media.py yjc --follow"),
    chain("ir_media_mizan", "ir_media.py mizan --follow"),
    chain("ir_media_kayhan", "ir_media.py kayhan --follow"),
    chain("ir_media_javan", "ir_media.py javan --follow"),
    chain("ir_media_defapress", "ir_media.py defapress --follow"),
    chain("ir_media_sobhesadegh", "ir_media.py sobhesadegh --follow"),
    chain("ir_media_snn", "ir_media.py snn --follow"),
    chain("ir_media_basijnews", "ir_media.py basijnews --follow"),
    chain("ir_media_mashregh", "ir_media.py mashregh --follow"),
    chain("ir_media_icana", "ir_media.py icana --follow"),
    chain("ir_media_tasnim", "ir_media.py tasnim --follow"),
    chain("ir_media_nournews", "ir_media.py nournews --follow"),
    chain("ir_media_jamejam", "ir_media.py jamejam --follow"),
    chain("ir_media_iqna", "ir_media.py iqna --follow"),
    chain("ir_media_shana", "ir_media.py shana --follow"),
    chain("ir_mfa_en", "ir_mfa.py"),
    chain("sy_sana", Step(("sy_sana.py", "--part", "new"), NEW), "sy_sana.py --part archive"),
    chain("sy_mofa", "sy_mofa.py"),
    chain("ve_mppre", "ve_mppre.py"),
    chain("cu_granma", "cu_granma.py"),
    chain("cu_minrex", "cu_minrex.py"),
    chain("by_belta_en", "by_belta.py --follow"),
    chain("cn_mfa_live", "cn_mfa_live.py --follow"),
    chain("cn_mfa_listings", "cn_mfa.py --part listings --follow"),
    chain("cn_mfa_archive", "cn_mfa.py --part archive --follow"),
    chain("cn_mnd", "cn_mnd.py --follow --wayback"),
    chain("cn_tao", "cn_tao.py --follow"),
    chain("cn_xinhua_en", "cn_xinhua.py --lang en --follow"),
    chain("cn_xinhua_zh", "cn_xinhua.py --lang zh --follow"),
    chain("cn_peoples_daily_epaper", "cn_peoples_daily.py epaper --follow"),
    chain("cn_peoples_daily_en", "cn_peoples_daily.py en --follow"),
    chain("cn_globaltimes", "cn_globaltimes.py --follow"),
    chain("cn_cgtn", "cn_cgtn.py --follow"),
    chain("cn_chinadaily_en", "cn_chinadaily.py --lang en --follow"),
    chain("cn_chinadaily_zh", "cn_chinadaily.py --lang zh --follow"),
    chain("cn_chinamil", "cn_chinamil.py --follow"),
    chain("cn_chinamil_wayback", "cn_chinamil.py --wayback"),
    chain("cn_govcn_zh", "cn_govcn.py --parts zhfeeds,gazette --follow"),
    chain("cn_govcn_en", "cn_govcn.py --parts en --follow"),
    chain("cn_govcn_wayback", "cn_govcn.py --wayback"),
    chain("cn_qiushi", "cn_qiushi.py --follow"),
    chain("cn_embassy", "cn_embassy.py --follow"),
    chain("cn_ccg", "cn_ccg.py --follow"),
    chain("ru_duma", "ru_duma.py --follow --start earliest"),
    chain("ru_rg_ru", "ru_sitemap_media.py rg_ru --follow"),
    chain("ru_vesti_ru", "ru_sitemap_media.py vesti_ru --follow"),
    chain("ru_government_ru", "ru_gov_sites.py government_ru --follow"),
    chain("ru_council_ru", "ru_gov_sites.py council_ru --follow"),
    chain("ru_1tv_ru", "ru_sitemap_media.py 1tv_ru --follow"),
    chain("ru_premier_archive_ru", "ru_gov_sites.py premier_archive_ru"),
    chain("ir_media_khabaronline", "ir_media.py khabaronline --follow"),
    chain("ir_media_hamshahri", "ir_media.py hamshahri --follow"),
    chain("ir_media_abna", "ir_media.py abna --follow"),
    chain("ir_media_abna_en", "ir_media.py abna_en --follow"),
    chain("ir_media_hawzah", "ir_media.py hawzah --follow"),
    chain("ir_media_quds", "ir_media.py quds --follow"),
    chain("ir_media_ettelaat", "ir_media.py ettelaat --follow"),
    chain("ir_media_rasa", "ir_media.py rasa --follow"),
    chain("ir_parstoday", "ir_more.py parstoday --follow"),
    chain("ir_irannewspaper", "ir_more.py irannewspaper --follow"),
    chain("cn_media_chinanews", "cn_media.py chinanews --follow"),
    chain("cn_media_ecns", "cn_media.py ecns --follow"),
    chain("cn_media_cctv", "cn_media.py cctv --follow"),
    chain("cn_media_cctv_en", "cn_media.py cctv_en --follow"),
    chain("cn_media_xwlb", "cn_media.py xwlb --follow"),
    chain("cn_media_huanqiu", "cn_media.py huanqiu --follow"),
    chain("cn_media_guancha", "cn_media.py guancha --follow"),
    chain("ru_tvzvezda_ru", "ru_more_media.py tvzvezda_ru --follow"),
    chain("ru_redstar_ru", "ru_more_media.py redstar_ru --follow"),
    chain("ru_government_archive_ru", "ru_more_media.py government_archive_ru"),
]
HTTP_PAT = {"http_403": re.compile(r"\b403\b"), "http_429": re.compile(r"\b429\b"),
            "http_5xx": re.compile(r"\b5\d\d\b(?!\d)"), "timeout": re.compile(r"timed? ?out", re.I),
            "robots": re.compile(r"robots", re.I), "errors": re.compile(r"\b(ERROR|Traceback)\b")}


def doc_lines() -> Dict[str, int]:
    """docs/<CC>/<source>.jsonl -> number of complete lines."""
    out = {}
    for p in sorted((ROOT / "docs").glob("*/*.jsonl")):
        with p.open("rb") as f:
            out[p.relative_to(ROOT / "docs").as_posix()] = sum(buf.count(b"\n") for buf in iter(lambda: f.read(1 << 22), b""))
    return out


def repair_tails() -> List[str]:
    """Truncate a docs file whose last line has no newline (cut by a kill) back to its last complete line."""
    fixed = []
    for p in (ROOT / "docs").glob("*/*.jsonl"):
        size = p.stat().st_size
        if not size:
            continue
        with p.open("rb+") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) == b"\n":
                continue
            f.seek(0)
            data = f.read()
            f.truncate(data.rfind(b"\n") + 1)
        fixed.append(p.relative_to(ROOT).as_posix())
    return fixed


def run_step(step: Step, log, deadline: float, grace: int, py: str) -> Tuple[str, float]:
    """Run one collector command until it exits or its deadline. Returns (outcome, seconds)."""
    t0 = time.time()
    stop_at = deadline if step.budget is None else min(deadline, t0 + step.budget)
    if stop_at - t0 < 60:
        return "skipped (no time left)", 0.0
    log.write(f"\n==== {datetime.now(timezone.utc).isoformat(timespec='seconds')} start {' '.join(step.args)}\n")
    log.flush()
    proc = subprocess.Popen([py, f"collectors/{step.args[0]}", *step.args[1:]], cwd=ROOT, stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=True)
    try:
        rc = proc.wait(timeout=max(stop_at - time.time(), 1))
        outcome = "ok" if rc == 0 else f"exit {rc}"
    except subprocess.TimeoutExpired:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=grace)
            outcome = "time box (stopped cleanly)"
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            outcome = "time box (killed)"
    log.write(f"==== {datetime.now(timezone.utc).isoformat(timespec='seconds')} end: {outcome}\n")
    log.flush()
    return outcome, time.time() - t0


def run_chain(c: Chain, deadline: float, grace: int, logdir: Path, py: str) -> dict:
    out = {"chain": c.name, "steps": []}
    path = logdir / f"{c.name}.log"
    with path.open("a", encoding="utf-8") as log:
        for s in c.steps:
            outcome, sec = run_step(s, log, deadline, grace, py)
            out["steps"].append({"cmd": " ".join(s.args), "outcome": outcome, "seconds": round(sec)})
            if outcome.startswith("time box") and s.budget is None:
                break
    text = path.read_text("utf-8", "ignore")
    out["log_counts"] = {k: len(p.findall(text)) for k, p in HTTP_PAT.items()}
    return out


def markdown(rep: dict) -> str:
    lines = [f"### Collection ({rep['minutes']} min, budget {rep['budget_min']} min)", "",
             f"Documents added: **{rep['docs_added_total']}**" + (f"; repaired tails: {rep['repaired']}" if rep["repaired"] else ""),
             "", "| source file | added |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(rep["docs_added"].items(), key=lambda kv: -kv[1]) if v]
    lines += ["", "| chain | outcome | min | 403 | 429 | 5xx | timeouts | errors |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for c in rep["chains"]:
        oc = "; ".join(s["outcome"] for s in c["steps"])
        m = round(sum(s["seconds"] for s in c["steps"]) / 60)
        k = c["log_counts"]
        lines.append(f"| {c['chain']} | {oc} | {m} | {k['http_403']} | {k['http_429']} | {k['http_5xx']} | {k['timeout']} | {k['errors']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--budget", type=int, default=int(os.environ.get("COLLECT_BUDGET_S", 13500)),
                    help="global seconds for the whole collection (default 13500 = 3 h 45 min)")
    ap.add_argument("--grace", type=int, default=90, help="seconds between SIGTERM and SIGKILL")
    ap.add_argument("--only", default="", help="comma list of chain-name prefixes to run")
    ap.add_argument("--skip", default=os.environ.get("COLLECT_SKIP", ""), help="comma list of chain names to skip")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    chains = [c for c in CHAINS if (not a.only or c.name.startswith(tuple(a.only.split(","))))
              and c.name not in set(filter(None, a.skip.split(",")))]
    if a.list:
        for c in chains:
            print(c.name, " -> ".join(" ".join(s.args) for s in c.steps))
        return
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logdir = ROOT / "logs" / "ci" / day
    logdir.mkdir(parents=True, exist_ok=True)
    for old in sorted(d for d in logdir.parent.iterdir() if d.is_dir())[:-14]:  # keep the last 14 runs
        for f in old.iterdir():
            f.unlink()
        old.rmdir()
    (ROOT / "state").mkdir(exist_ok=True)
    py = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else sys.executable
    t0 = time.time()
    deadline = t0 + a.budget
    repaired = repair_tails()
    before = doc_lines()
    print(f"collecting: {len(chains)} chains in parallel, budget {a.budget // 60} min", flush=True)
    with ThreadPoolExecutor(len(chains) or 1) as ex:
        results = list(ex.map(lambda c: run_chain(c, deadline, a.grace, logdir, py), chains))
    repaired += repair_tails()
    after = doc_lines()
    added = {k: after.get(k, 0) - before.get(k, 0) for k in sorted(set(after) | set(before))}
    rep = {"date": day, "minutes": round((time.time() - t0) / 60), "budget_min": a.budget // 60, "chains": results,
           "docs_added": added, "docs_added_total": sum(added.values()), "repaired": repaired}
    (logdir / "summary.json").write_text(json.dumps(rep, indent=1))
    md = markdown(rep)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
