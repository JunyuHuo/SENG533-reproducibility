#!/usr/bin/env python3
"""Summarize pipeline results into CSVs for analysis.

    python3 parse_results.py <results dir> [-o <output dir>]

Finds every run (a directory with run.json) under <results dir> and writes:
  runs.csv        one row per measured run: throughput, response times, utilization, checks
  timeseries.csv  YCSB's 10-second status lines for warm-up and measured runs
                  (used in the pilot to choose the warm-up length)

Only the standard library is used, so it runs on the VMs as well as on laptops.
"""
import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

OP_SECTIONS = ("READ", "UPDATE", "INSERT", "READ-FAILED", "UPDATE-FAILED", "INSERT-FAILED")
LITTLE_TOLERANCE_PCT = 5.0
LG_CPU_LIMIT_PCT = 70.0
SAR_EDGE_S = 3  # trim JVM start-up and shutdown from the utilization window

STATUS_RE = re.compile(
    r"^(?P<ts>\S+ \S+) (?P<t>\d+) sec: (?P<ops>\d+) operations; (?P<rate>[\d.]+|\S+) current ops/sec;")
STATUS_OP_RE = re.compile(r"\[(?P<op>[A-Z-]+): Count=(?P<count>\d+), Max=\d+, Min=\d+, "
                          r"Avg=(?P<avg>[\d.]+), 90=\d+, 99=(?P<p99>\d+)")


def parse_ycsb(path):
    """Return {section: {metric: value}} from YCSB's summary lines."""
    out = {}
    for line in path.read_text(errors="replace").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3 and parts[0].startswith("[") and parts[0].endswith("]"):
            try:
                out.setdefault(parts[0][1:-1], {})[parts[1]] = float(parts[2])
            except ValueError:
                pass
    return out


def parse_sar(path, start, end):
    """Return sadf -d rows (dicts) whose timestamp is inside [start, end]."""
    if not path.exists():
        return []
    header, rows = None, []
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("# hostname"):
            header = line[2:].split(";")
        elif header and line and not line.startswith("#"):
            row = dict(zip(header, line.split(";")))
            ts = datetime.strptime(row["timestamp"][:19], "%Y-%m-%d %H:%M:%S")
            ts = ts.replace(tzinfo=timezone.utc).timestamp()
            if start <= ts <= end:
                rows.append(row)
    return rows


def steady_state(err_path, measure_s):
    """Throughput (ops/s) and mean response time (us) over the same steady window.

    The window runs from the first to the last status line while client threads are
    running, so it excludes connection set-up at the start and driver shutdown at the
    end (YCSB's RunTime includes about 2 s of shutdown for Cassandra). Status lines
    report each operation's count and mean latency per interval.
    """
    points = []
    if err_path.exists():
        for line in err_path.read_text(errors="replace").splitlines():
            m = STATUS_RE.match(line)
            if m and 0 < int(m["t"]) < measure_s:
                ts = datetime.strptime(m["ts"], "%Y-%m-%d %H:%M:%S:%f").timestamp()
                ops = [(int(o["count"]), float(o["avg"])) for o in STATUS_OP_RE.finditer(line)
                       if o["op"] != "CLEANUP"]
                points.append((ts, int(m["ops"]), ops))
    if len(points) < 2:
        return None, None
    (t0, ops0, _), (t1, ops1, _) = points[0], points[-1]
    counted = [op for _, _, ops in points[1:] for op in ops]
    n = sum(c for c, _ in counted)
    r_us = sum(c * avg for c, avg in counted) / n if n else None
    return (ops1 - ops0) / (t1 - t0), r_us


def mean(rows, col):
    vals = [float(r[col]) for r in rows if col in r]
    return sum(vals) / len(vals) if vals else None


def busiest(rows, key, score_cols, skip):
    """Pick the device/interface with the highest mean score, return (name, its rows)."""
    groups = {}
    for r in rows:
        if not any(r[key].startswith(s) for s in skip):
            groups.setdefault(r[key], []).append(r)
    if not groups:
        return None, []
    name = max(groups, key=lambda g: sum(mean(groups[g], c) or 0 for c in score_cols))
    return name, groups[name]


def summarize_run(run_dir):
    meta = json.loads((run_dir / "run.json").read_text())
    y = parse_ycsb(run_dir / "run.out")
    row = dict(meta)
    row["run_dir"] = str(run_dir)

    overall = y.get("OVERALL", {})
    row["runtime_s"] = overall.get("RunTime(ms)", 0) / 1000 or None
    row["ycsb_throughput_ops_s"] = overall.get("Throughput(ops/sec)")
    x, r_steady_us = steady_state(run_dir / "run.err", meta["measure_s"])
    x = x or row["ycsb_throughput_ops_s"]
    row["throughput_ops_s"] = x

    total_ops = weighted = errors = 0
    for sec in OP_SECTIONS:
        m = y.get(sec)
        if not m:
            continue
        errors += sum(v for k, v in m.items() if k.startswith("Return=") and k != "Return=OK")
        ops = m.get("Operations", 0)
        if not ops:  # empty sections report AverageLatency = NaN
            continue
        total_ops += ops
        weighted += ops * m.get("AverageLatency(us)", 0)
        if sec in ("READ", "UPDATE"):
            p = sec.lower()
            row[f"{p}_ops"] = ops
            row[f"{p}_mean_us"] = m.get("AverageLatency(us)")
            row[f"{p}_p95_us"] = m.get("95thPercentileLatency(us)")
            row[f"{p}_p99_us"] = m.get("99thPercentileLatency(us)")
    row["failed_ops"] = sum(y.get(s, {}).get("Operations", 0) for s in OP_SECTIONS if s.endswith("FAILED"))
    row["error_returns"] = errors
    row["ycsb_mean_response_us"] = weighted / total_ops if total_ops else None
    r_mean_us = r_steady_us or row["ycsb_mean_response_us"]
    row["mean_response_us"] = r_mean_us

    # Little's Law: with zero think time, threads = X * R.
    flags = []
    if x and r_mean_us:
        n_est = x * r_mean_us / 1e6
        row["littles_n"] = n_est
        row["littles_err_pct"] = 100 * (n_est - meta["threads"]) / meta["threads"]
        if abs(row["littles_err_pct"]) > LITTLE_TOLERANCE_PCT:
            flags.append("littles_law")
    else:
        flags.append("no_results")
    if meta.get("exit_code"):
        flags.append("ycsb_exit")
    if errors or row["failed_ops"]:
        flags.append("op_errors")

    lo, hi = meta["start_epoch"] + SAR_EDGE_S, meta["end_epoch"] - SAR_EDGE_S
    # %iowait is idle time spent waiting on the disk, so it is not CPU busy time.
    cpu = parse_sar(run_dir / "sar_db_cpu.csv", lo, hi)
    if cpu:
        row["db_cpu_util_pct"] = 100 - mean(cpu, "%idle") - mean(cpu, "%iowait")
        row["db_cpu_iowait_pct"] = mean(cpu, "%iowait")
        row["db_cpu_steal_pct"] = mean(cpu, "%steal")
    disk_name, disk = busiest(parse_sar(run_dir / "sar_db_disk.csv", lo, hi), "DEV", ["%util"], ["loop"])
    if disk:
        row["db_disk"] = disk_name
        for col, name in (("%util", "util_pct"), ("tps", "tps"), ("rkB/s", "read_kB_s"),
                          ("wkB/s", "write_kB_s"), ("await", "await_ms")):
            row[f"db_disk_{name}"] = mean(disk, col)
    net_name, net = busiest(parse_sar(run_dir / "sar_db_net.csv", lo, hi), "IFACE",
                            ["rxkB/s", "txkB/s"], ["lo"])
    if net:
        row["db_net"] = net_name
        row["db_net_rx_kB_s"] = mean(net, "rxkB/s")
        row["db_net_tx_kB_s"] = mean(net, "txkB/s")
    mem = parse_sar(run_dir / "sar_db_mem.csv", lo, hi)
    if mem:
        row["db_mem_used_pct"] = mean(mem, "%memused")
    lg_cpu = parse_sar(run_dir / "sar_lg_cpu.csv", lo, hi)
    if lg_cpu:
        row["lg_cpu_util_pct"] = 100 - mean(lg_cpu, "%idle") - mean(lg_cpu, "%iowait")
        if row["lg_cpu_util_pct"] > LG_CPU_LIMIT_PCT:
            flags.append("lg_cpu")
    if not cpu or not lg_cpu:
        flags.append("no_sar")

    row["flags"] = " ".join(flags)
    return row


def timeseries(run_dir, meta):
    for phase in ("warmup", "run"):
        path = run_dir / f"{phase}.err"
        if not path.exists():
            continue
        for line in path.read_text(errors="replace").splitlines():
            m = STATUS_RE.match(line)
            if not m:
                continue
            row = {k: meta[k] for k in ("setup", "db", "server_size", "launch", "workload", "threads")}
            row.update(phase=phase, t_s=int(m["t"]), cumulative_ops=int(m["ops"]))
            try:
                row["current_ops_s"] = float(m["rate"])
            except ValueError:
                row["current_ops_s"] = None
            for op in STATUS_OP_RE.finditer(line):
                if op["op"] in ("READ", "UPDATE"):
                    p = op["op"].lower()
                    row[f"{p}_avg_us"] = float(op["avg"])
                    row[f"{p}_p99_us"] = int(op["p99"])
            yield row


def write_csv(path, rows):
    cols = []
    for r in rows:
        cols += [k for k in r if k not in cols]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", type=Path)
    ap.add_argument("-o", "--out", type=Path, help="output directory (default: the results dir)")
    args = ap.parse_args()
    out = args.out or args.results
    out.mkdir(parents=True, exist_ok=True)

    run_dirs = sorted(p.parent for p in args.results.rglob("run.json"))
    if not run_dirs:
        sys.exit(f"No runs (run.json) found under {args.results}")

    runs, series = [], []
    for d in run_dirs:
        row = summarize_run(d)
        runs.append(row)
        series += timeseries(d, row)
    runs.sort(key=lambda r: (r["db"], r["server_size"], r["launch"], r["workload"], r["threads"]))
    write_csv(out / "runs.csv", runs)
    write_csv(out / "timeseries.csv", series)

    print(f"{len(runs)} runs -> {out / 'runs.csv'}, {out / 'timeseries.csv'}")
    print(f"{'db':<10}{'workload':<13}{'thr':>4}{'ops/s':>11}{'R ms':>8}{'p99 rd':>9}{'p99 up':>9}"
          f"{'dbCPU%':>8}{'disk%':>7}{'lgCPU%':>8}  flags")
    fmt = lambda v, s=1, d=1: f"{v / s:.{d}f}" if isinstance(v, (int, float)) else "-"
    for r in runs:
        print(f"{r['db']:<10}{r['workload']:<13}{r['threads']:>4}"
              f"{fmt(r.get('throughput_ops_s'), d=0):>11}{fmt(r.get('mean_response_us'), 1000, 2):>8}"
              f"{fmt(r.get('read_p99_us'), 1000, 2):>9}{fmt(r.get('update_p99_us'), 1000, 2):>9}"
              f"{fmt(r.get('db_cpu_util_pct')):>8}{fmt(r.get('db_disk_util_pct')):>7}"
              f"{fmt(r.get('lg_cpu_util_pct')):>8}  {r['flags']}")


if __name__ == "__main__":
    main()
