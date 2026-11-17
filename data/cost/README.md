# Historical OCI cost and usage samples

These two CSVs were supplied by the project team from OCI Cost Analysis exports.
They retain the supplied values and generic resource aliases; no resource OCIDs,
server addresses, account identifiers, or keys appear in these files. The CSVs
are copied byte-for-byte from the supplied sanitized files. They are billing
samples, not a complete account statement or the charges for specific YCSB runs.

Run from the repository root:

```sh
python3 analysis/cost.py
python3 analysis/summarize.py
```

The derivation checks `usage/billedQuantity * cost/unitPrice == cost/myCost`
using decimal arithmetic, CAD currency, Toronto region, one-hour time buckets,
and the expected OCPU and memory allocations. In each accepted interval both
VMs have 2 OCPU-hours each; baseline memory usage is 16 + 16 GiB-hours and
upgraded memory usage is 16 + 32 GiB-hours.

| Configuration | Complete compute hours (UTC start) | Combined CAD/hour |
| --- | --- | --- |
| Baseline | 2026-10-31 05:00, 06:00, 07:00 | 0.2726048 |
| Upgraded | 2026-11-02 23:00; 2026-11-03 00:00, 01:00 | 0.3040592 |

The upgraded 22:00 and 02:00 buckets contain only 0.5 OCPU-hours per VM, with
proportional memory usage. They are excluded from the full-hour comparison.
Baseline buckets without compute rows do not establish a zero compute rate.
All exported storage and network costs are zero in these particular samples;
this does not establish that such services are always free.

The hourly increase is 11.53846%. Multiplying the hourly rates by 24 gives daily
equivalents of CAD 6.5425152 and 7.2974208, assuming continuous operation. These
are not totals measured from complete daily statements. These samples do not
establish the exact date/time the VM was resized.

The original paper used slightly truncated hourly rates (0.272604 and 0.304058).
The analysis now derives the exact exported rates; all four cost-per-million
entries at six decimals and reductions at one decimal remain unchanged.
