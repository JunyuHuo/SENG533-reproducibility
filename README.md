# SENG 533 NoSQL Benchmark Reproducibility Package

This package accompanies *Performance Evaluation of NoSQL Databases Hosted on Cloud Infrastructure* by Junyu Huo, Damon Mazurek, Benny Ndabaramiye, Mykola Viktorovskyi, and Vihang Shah (University of Calgary). It contains benchmark measurements from all **162 runs** (126 baseline and 36 upgraded) and the pipeline used to collect and summarize them.

## Reproduce the reported results

Run from the repository root with Python 3.8 or later. This workflow needs neither a cloud account nor Python packages.

```sh
python3 analysis/summarize.py > summary.json
```

The JSON includes throughput and mean response-time curves, observed baseline peaks with sample standard deviations and 95% Student's t intervals, matched-thread memory upgrade comparisons, read/update tail latency and resource metrics, operation errors, and Little's Law discrepancies. The cost estimates use the paper's reported historical rates of CAD 0.272604 and 0.304058 per hour. They compare each baseline peak with the corresponding repeated upgraded point. The underlying account billing export is outside this package.

To rebuild both CSVs from the measurements:

```sh
mkdir -p build
tar -xzf data/measurements.tar.gz -C build
TZ=UTC python3 pipeline/parse_results.py build/oci-single -o build
python3 analysis/summarize.py build/runs.csv > build/summary.json
cmp summary.json build/summary.json
```

The archive contains YCSB status and summary records, `sadf` measurements, and run metadata. Each file retains the observations used by the parser. Collection hostnames are replaced with role labels. `runs.csv` uses archive-relative run paths. A generated CSV uses the directory you supplied to the parser in the `run_dir` column. `timeseries.csv` contains 5,184 status rows from the discarded warm-ups and measured runs.

## Verify completeness and rebuild figures

```sh
python3 analysis/verify.py
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-figures.txt
python analysis/plot.py
```

The verifier checks SHA-256 checksums, every expected configuration, all 162 archived run records, all fields in both regenerated CSVs, and the summary. It does not treat operation failures as missing runs: the three failed operations in two Cassandra runs remain in the data and the paper.

The plotter rebuilds all nine data charts as vector PDFs and PNGs plus `plot-data.csv`. [Figure provenance](figures/README.md) maps every paper graphic to its source and explains how to build the two editable TikZ diagrams. [Verification notes](VERIFICATION.md) document the checked scope and remaining limitations.

## Repeat data collection

Provision two Ubuntu 22.04.5 LTS x86-64 VMs on a private network. Use 4 vCPUs and a 100 GB boot volume per VM, with 16 GiB RAM on both VMs for the baseline. For Experiment 2, increase only the database VM to 32 GiB RAM. Use MongoDB 7.0.45, Cassandra 4.1.12, Java 11, sysstat 12.5.2, and [YCSB 0.17.0](https://github.com/brianfrankcooper/YCSB/releases/tag/0.17.0) with both the `mongodb` and `cassandra-cql` bindings. Check the exact database package versions before running. The bootstrap script selects the available package releases in the 7.0 and 4.1 series.

On a dedicated database VM, install the switcher and configure the two database services. Substitute the two private addresses from your own environment. The installer changes database and firewall settings and initializes the YCSB keyspace.

```sh
sudo install -m 755 db-server-setup/seng533-switch-db /usr/local/sbin/seng533-switch-db
sudo env DB_PRIVATE_IP="$DB_PRIVATE_IP" LOAD_GENERATOR_IP="$LOAD_GENERATOR_IP" \
  bash db-server-setup/install_db_server.sh
```

On the load generator, install Java, sysstat, and Python 3. Install the YCSB release and populate `DB_HOST` and `DB_SSH_KEY` with your own private database VM address and SSH key path. Verify that the load generator can SSH to `ubuntu@$DB_HOST` and run the required privileged commands on the dedicated database VM. The pipeline starts and stops services, clears the OS page cache, and recreates benchmark data. Use a VM reserved for this experiment.

```sh
export YCSB_HOME="$HOME/ycsb-0.17.0"
bash pipeline/run_launch.sh pipeline/config/cassandra-base-L1.conf
```

The 12 configuration files record each original launch. Complete the six `base` launches before resizing the database VM, then use the six `upgraded` launch configurations. Launch 1 sweeps all seven thread counts. In the upgraded experiment, launches 2 and 3 repeat two selected workload/thread pairs per database. The workload definitions include 95/5, 50/50, and 5/95 read/update ratios. Each launch loads ten million records, warms the read cache for 600 seconds, and uses a 60-second discarded warm-up and a 240-second measurement for each configuration. MongoDB uses `w=1`. Cassandra uses replication factor 1 and consistency `ONE` for reads and updates.

## Files

| Path | Contents |
| --- | --- |
| `data/measurements.tar.gz` | Sanitized parser inputs for all 162 runs |
| `data/runs.csv`, `data/timeseries.csv` | Reference aggregate and time-series datasets |
| `pipeline/config/`, `pipeline/workloads/` | Recorded launch settings and workload definitions |
| `pipeline/run_launch.sh`, `pipeline/parse_results.py` | Collection and aggregation |
| `analysis/summarize.py` | Recomputes the principal paper results |
| `analysis/verify.py` | Checks integrity and rebuilds both complete datasets |
| `analysis/plot.py`, `requirements-figures.txt` | Reproducible plotting and tested dependency versions |
| `figures/` | All paper data plots, plotting values, and conceptual diagram source |
| `SHA256SUMS` | Integrity checksums for datasets, plotting values, and figures |
| `db-server-setup/` | Ubuntu database setup, verification, and service switcher |
