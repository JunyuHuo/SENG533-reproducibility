#!/usr/bin/env python3
"""Recompute the paper's run checks, result summaries, and compute cost estimates."""
import argparse
from collections import Counter, defaultdict
import csv
import json
from math import sqrt
from pathlib import Path
from statistics import mean, median, stdev
from cost import hourly_rates

ROOT = Path(__file__).resolve().parents[1]
THROUGHPUT = 'throughput_ops_s'
RATES = hourly_rates()  # Derived from the sanitized historical OCI usage exports.


def values(runs, field):
    return [float(row[field]) for row in runs]


def throughput(runs):
    observations = values(runs, THROUGHPUT)
    average = mean(observations)
    result = {'n': len(runs), 'ops_s': average}
    if len(runs) == 3:
        sd = stdev(observations)
        margin = 4.302652729911275 * sd / sqrt(3)
        result.update(sample_sd=sd, ci95=[average - margin, average + margin])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', type=Path, nargs='?', default=ROOT / 'data/runs.csv')
    args = parser.parse_args()
    with args.runs.open(newline='') as stream:
        runs = list(csv.DictReader(stream))
    counts = Counter(row['server_size'] for row in runs)
    if counts != {'base': 126, 'upgraded': 36}:
        parser.error(f'Expected 126 baseline and 36 upgraded runs, got {dict(counts)}')
    groups = defaultdict(list)
    for row in runs:
        groups[row['db'], row['workload'], row['server_size'], int(row['threads'])].append(row)

    baseline, peaks, upgrades = [], [], []
    for db, workload in sorted({key[:2] for key in groups}):
        curve = []
        for (database, mix, size, threads), group in sorted(groups.items()):
            if (database, mix, size) == (db, workload, 'base'):
                curve.append(dict(db=db, workload=workload, threads=threads,
                                  mean_response_ms=mean(values(group,'mean_response_us')) / 1000,
                                  **throughput(group)))
        baseline.extend(curve)
        peak = max(curve, key=lambda point: point['ops_s'])
        peaks.append(peak)
        for (database, mix, size, threads), after in sorted(groups.items()):
            if (database, mix, size) != (db, workload, 'upgraded') or len(after) != 3:
                continue
            before = groups[db, workload, 'base', threads]
            base, upgrade = throughput(before), throughput(after)
            base_cost = RATES['base'] * 1e6 / (3600 * peak['ops_s'])
            upgrade_cost = RATES['upgraded'] * 1e6 / (3600 * upgrade['ops_s'])
            details = {}
            for name, group in [('baseline', before), ('upgraded', after)]:
                details[name] = {metric: mean(values(group, metric)) for metric in (
                    'read_p95_us', 'read_p99_us', 'update_p95_us', 'update_p99_us',
                    'db_cpu_util_pct', 'db_cpu_iowait_pct', 'db_disk_util_pct',
                    'db_mem_used_pct', 'lg_cpu_util_pct')}
            upgrades.append(dict(db=db, workload=workload, matched_threads=threads,
                baseline=base, upgraded=upgrade, change_pct=100*(upgrade['ops_s']/base['ops_s']-1),
                metrics=details, cost_baseline_peak_threads=peak['threads'],
                baseline_cad_per_million=base_cost, upgraded_cad_per_million=upgrade_cost,
                cost_reduction_pct=100*(1-upgrade_cost/base_cost)))

    discrepancies = [abs(value) for value in values(runs,'littles_err_pct')]
    failed = [row for row in runs if float(row['failed_ops']) or float(row['error_returns'])]
    summary = dict(run_counts=dict(counts), baseline_curves=baseline, baseline_peaks=peaks,
        matched_upgrades=upgrades, little_law=dict(max_abs_pct=max(discrepancies),
            mean_abs_pct=mean(discrepancies), median_abs_pct=median(discrepancies),
            above_5_pct=sum(value > 5 for value in discrepancies)),
        process_errors=sum(int(row['exit_code']) != 0 for row in runs),
        operation_error_runs=[{field: row[field] for field in (
            'db','server_size','launch','workload','threads','failed_ops','error_returns','exit_code')}
            for row in failed])
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
