# Verification record — 2026-11-15

## Checked

- Complete experiment matrix: 126 baseline + 36 upgraded runs, with unique run keys and exactly the recorded selected repetitions.
- Clean extraction of the 1,620 archived parser inputs regenerated all 162 aggregate rows and all 5,184 time-series rows. Every field matched the reference CSVs after normalizing only `run_dir`.
- The regenerated summary matched the reference-data summary exactly.
- 160 numeric cells in the paper's baseline, upgrade, p99, resource, and cost tables were compared with the recomputed measurements. Four cost entries were corrected at the final displayed decimal places using unrounded throughput; reported percentage reductions and conclusions did not change.
- A fresh Python 3.12 virtual environment installed the pinned plotting dependencies. Regeneration from the rebuilt CSV produced byte-identical copies of all 18 committed PNG/PDF outputs on the verification platform.
- All nine data plots were rebuilt from `runs.csv`; the 124 plotting records expose the means, SDs, sample sizes, and unit conversions. Both conceptual diagrams have editable TikZ source.
- Gitleaks v8.30.1 found no secrets in the published Git history or the decompressed measurement archive. A separate address/identifier check found no deployment IPs, OCI identifiers, private keys, public SSH keys, or credential-bearing URLs. Generic loopback/bind addresses are intentional. This is a scoped audit, not a guarantee against every possible disclosure.
- Historical raw logs were reduced to the parser inputs needed for the numerical results. Hostnames were replaced with role labels, and archive user/group metadata was cleared. No private source-repository history, credentials, deployment notes, or account screenshots were copied into this repository.

## Reproduction boundaries

Numerical reproduction means rerunning the parser, summary, and plotter on the
archived observations. It does **not** mean a new cloud deployment will produce
identical performance. Cloud scheduling, storage, caches, and database background
work vary. No new cloud collection was performed for this verification.

The package retains all measurements consumed by the parser, but does not contain
the ten-million-record database dump, a full VM image, per-request raw latency
samples, account billing export, or every diagnostic line from the original logs.
YCSB generates the synthetic dataset. p95/p99 are recorded histogram percentiles;
a pooled percentile cannot be reconstructed from them.

The reported historical compute rates are explicit inputs to cost arithmetic.
The public package cannot independently substantiate those rates without the
billing export. Database installation scripts select the documented package
series; verify/install the stated exact versions before collecting new results.
The original VM image, kernel, storage settings, and dependency resolution are
not captured as an immutable environment.

The final report documents procedures, experimental results, and reflections,
including its single-node scope, limited upgraded repetitions, omitted upgraded
mixed workload, operation errors, differing durability semantics, and MVA as
future work. Meeting these documented content categories does not guarantee a
grade or substitute for any additional instructor rubric.
