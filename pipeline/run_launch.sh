#!/usr/bin/env bash
# One launch: fresh data for one database, then every workload x thread level.
# Runs on the load generator (experiment VM) and drives the DB VM over SSH.
#
#   ./run_launch.sh config/exp1-mongo.conf
#
# Per run: a warm-up YCSB run (discarded), then the measured YCSB run with sar
# recording on both VMs. Output: $RESULTS_ROOT/<setup>/<db>-<size>-L<launch>/.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${1:?usage: $0 <config file>}"

# Defaults; the config file can override any of these.
SETUP=oci-single
DB_HOST="${DB_HOST:-}"
DB_SSH_KEY="${DB_SSH_KEY:-$HOME/.ssh/seng533-db.key}"
YCSB_HOME="${YCSB_HOME:-$HOME/ycsb-0.17.0}"
RESULTS_ROOT="${RESULTS_ROOT:-$HOME/results}"
LOAD_THREADS=32
INITIAL_WARMUP_S=600
STATUS_INTERVAL_S=10
COMPACTION_TIMEOUT_S=1800
# shellcheck source=/dev/null
source "$CONFIG"
: "${DB:?} ${SERVER_SIZE:?} ${LAUNCH:?} ${WORKLOADS:?} ${RECORDCOUNT:?} ${WARMUP_S:?} ${MEASURE_S:?}"
: "${DB_HOST:?Set DB_HOST to your database VM private address}"
THREADS="${THREADS:-}"

case "$DB" in
  mongo)
    BINDING=mongodb
    DB_PROPS=(-p "mongodb.url=mongodb://$DB_HOST:27017/ycsb?w=1")
    DATA_DIR=/var/lib/mongodb
    ;;
  cassandra)
    BINDING=cassandra-cql
    DB_PROPS=(-p "hosts=$DB_HOST" -p port=9042
              -p cassandra.readconsistencylevel=ONE -p cassandra.writeconsistencylevel=ONE)
    DATA_DIR=/var/lib/cassandra/data/ycsb
    ;;
  *) echo "DB must be mongo or cassandra, got '$DB'" >&2; exit 2 ;;
esac

exec 9>/tmp/seng533-launch.lock
flock -n 9 || { echo "Another launch is already running on this machine." >&2; exit 1; }

LAUNCH_DIR="$RESULTS_ROOT/$SETUP/$DB-$SERVER_SIZE-L$LAUNCH"
if [[ -e "$LAUNCH_DIR" ]]; then
  echo "$LAUNCH_DIR already exists. Bump LAUNCH in the config or move the old results." >&2
  exit 1
fi
mkdir -p "$LAUNCH_DIR"
cp "$CONFIG" "$LAUNCH_DIR/launch.conf"
exec > >(tee -a "$LAUNCH_DIR/launch.log") 2>&1

log() { printf '%s  %s\n' "$(date -u +%FT%TZ)" "$*"; }
db() { ssh -n -i "$DB_SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 "ubuntu@$DB_HOST" "$@"; }

DB_SAR_PID= LG_SAR_PID=
cleanup() {
  [[ -n "$LG_SAR_PID" ]] && kill "$LG_SAR_PID" 2>/dev/null || true
  [[ -n "$DB_SAR_PID" ]] && db "kill $DB_SAR_PID" 2>/dev/null || true
}
trap cleanup EXIT
trap 'log "FAILED at line $LINENO: $BASH_COMMAND"' ERR

# ycsb <load|run> <workload> <threads> <output prefix> [extra YCSB args...]
# Runs are bounded by maxexecutiontime, so operationcount is just a large cap
# (CoreWorkload also requires it to be set when loading).
ycsb() {
  local mode=$1 workload=$2 threads=$3 out=$4; shift 4
  "$YCSB_HOME/bin/ycsb.sh" "$mode" "$BINDING" -s \
    -P "$PIPELINE_DIR/workloads/$workload" "${DB_PROPS[@]}" \
    -p recordcount="$RECORDCOUNT" -p operationcount=2000000000 \
    -p measurementtype=hdrhistogram -p hdrhistogram.percentiles=50,95,99,99.9 \
    -p status.interval="$STATUS_INTERVAL_S" \
    -threads "$threads" "$@" > "$out.out" 2> "$out.err"
}

# sar records every second on both VMs; count is a safety bound if a kill is missed.
start_sar() {
  local dir=$1 count=$((MEASURE_S + 120))
  DB_SAR_PID=$(db "mkdir -p /tmp/seng533 && rm -f /tmp/seng533/run.sa &&
    { nohup sar -o /tmp/seng533/run.sa 1 $count </dev/null >/dev/null 2>&1 & echo \$!; }")
  sar -o "$dir/lg.sa" 1 "$count" </dev/null >/dev/null 2>&1 &
  LG_SAR_PID=$!
}

stop_sar() {
  local dir=$1
  db "kill $DB_SAR_PID" 2>/dev/null || true
  kill "$LG_SAR_PID" 2>/dev/null || true
  wait "$LG_SAR_PID" 2>/dev/null || true
  DB_SAR_PID= LG_SAR_PID=
  sleep 1
  db "sadf -d /tmp/seng533/run.sa -- -u"       > "$dir/sar_db_cpu.csv"
  db "sadf -d /tmp/seng533/run.sa -- -d -p"    > "$dir/sar_db_disk.csv"
  db "sadf -d /tmp/seng533/run.sa -- -n DEV"   > "$dir/sar_db_net.csv"
  db "sadf -d /tmp/seng533/run.sa -- -r"       > "$dir/sar_db_mem.csv"
  sadf -d "$dir/lg.sa" -- -u     > "$dir/sar_lg_cpu.csv"
  sadf -d "$dir/lg.sa" -- -n DEV > "$dir/sar_lg_net.csv"
  rm -f "$dir/lg.sa"
}

# Cassandra compacts in the background; start each workload with none pending.
wait_compaction() {
  [[ "$DB" == cassandra ]] || return 0
  local deadline=$((SECONDS + COMPACTION_TIMEOUT_S)) pending
  while :; do
    pending=$(db "nodetool compactionstats" | awk -F': *' '/pending tasks/ {print $2+0}')
    [[ "$pending" == 0 ]] && { log "Compaction idle"; return 0; }
    if (( SECONDS > deadline )); then
      log "WARN: $pending compactions still pending after ${COMPACTION_TIMEOUT_S}s; continuing"
      return 0
    fi
    log "Waiting for $pending pending compactions"
    sleep 15
  done
}

db_ready() {
  if [[ "$1" == mongo ]]; then
    db "mongosh --quiet --host 127.0.0.1 --eval 'db.runCommand({ping: 1}).ok'" 2>/dev/null | grep -qx 1
  else
    db "cqlsh 127.0.0.1 9042 -e 'SELECT release_version FROM system.local'" >/dev/null 2>&1
  fi
}

# switch_db <mongo|cassandra|stop>. After write-heavy runs a mongod shutdown can
# outlast systemd's stop timeout; mongod is then killed and its next start spends
# minutes in recovery, longer than seng533-switch-db waits (60 s). So checkpoint
# mongod first to keep its shutdown short, and if the switch still gives up,
# keep waiting for the database to answer.
switch_db() {
  db "! systemctl is-active --quiet mongod ||
      mongosh --quiet --host 127.0.0.1 --eval 'db.adminCommand({fsync: 1})' >/dev/null || true"
  db "sudo seng533-switch-db $1" >/dev/null 2>&1 && return 0
  [[ "$1" != stop ]] || return 1
  log "$1 not ready when seng533-switch-db gave up; waiting up to 15 min"
  local deadline=$((SECONDS + 900))
  until db_ready "$1"; do
    (( SECONDS < deadline )) || { log "$1 did not start"; return 1; }
    sleep 10
  done
  log "$1 ready"
}

# Restart the database and drop the OS page cache so every launch starts cold.
cold_restart() {
  switch_db stop
  db "sync && echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null"
  switch_db "$DB"
}

# --- Preflight ---------------------------------------------------------------
# A config can override THREADS for one workload, e.g. THREADS_write_heavy="8"
# (Experiment 2 runs only each workload's peak thread count after the first launch).
threads_for() { local var="THREADS_${1//-/_}"; echo "${!var:-$THREADS}"; }
n_runs=0
for w in $WORKLOADS; do n_runs=$(( n_runs + $(wc -w <<<"$(threads_for "$w")") )); done
log "Launch $LAUNCH_DIR"
log "$n_runs runs; measured phase ~$(( n_runs * (WARMUP_S + MEASURE_S + 20) / 60 )) min plus load"
[[ -x "$YCSB_HOME/bin/ycsb.sh" ]] || { log "YCSB not found at $YCSB_HOME"; exit 1; }
[[ -r "$DB_SSH_KEY" ]] || { log "DB SSH key not found at $DB_SSH_KEY"; exit 1; }
command -v sar >/dev/null || { log "sysstat is not installed on the load generator"; exit 1; }
db true || { log "Cannot SSH to the DB server at $DB_HOST"; exit 1; }
for w in $WORKLOADS; do
  [[ -f "$PIPELINE_DIR/workloads/$w" ]] || { log "Unknown workload '$w'"; exit 1; }
  [[ -n "$(threads_for "$w")" ]] || { log "No thread counts set for '$w'"; exit 1; }
  for t in $(threads_for "$w"); do
    [[ "$t" =~ ^[0-9]+$ ]] || { log "Thread count '$t' for '$w' is not a number"; exit 1; }
  done
done

{
  echo "## launch"; echo "start_utc=$(date -u +%FT%TZ)"; echo "setup=$SETUP"
  echo "## load generator"; nproc; free -m; lscpu | grep 'Model name'
  java -version 2>&1 | head -1; echo "ycsb=$YCSB_HOME"
  echo "## db server"
  db "nproc; free -m; lscpu | grep 'Model name'; lsblk -d -o NAME,SIZE,ROTA | grep -v loop;
      dpkg-query -W mongodb-org-server cassandra sysstat"
} > "$LAUNCH_DIR/env.txt"

# --- Fresh data ----------------------------------------------------------------
log "Switching DB server to $DB"
switch_db "$DB"

log "Clearing old data"
if [[ "$DB" == mongo ]]; then
  db "mongosh --quiet --host 127.0.0.1 ycsb --eval 'db.usertable.drop()'" >/dev/null
else
  db "cqlsh 127.0.0.1 9042 --request-timeout=300 -e 'TRUNCATE ycsb.usertable' &&
      nodetool clearsnapshot --all >/dev/null"
fi

log "Loading $RECORDCOUNT records with $LOAD_THREADS threads"
load_start=$SECONDS
# Without retries, a YCSB load thread stops for good after one failed insert
# (Cassandra times out a few writes under load), so the load ends early.
# 60 retries x 3 s rides out stalls of about 3 min; 10 retries was too few for
# Cassandra on the 32 GB server (bigger heap, longer memtable flushes).
ycsb load mixed "$LOAD_THREADS" "$LAUNCH_DIR/load" \
  -p core_workload_insertion_retry_limit=60 -p core_workload_insertion_retry_interval=3 \
  || { log "Load failed, see load.err"; exit 1; }
inserted=$(awk -F', ' '$1 == "[INSERT]" && $2 == "Return=OK" {print $3}' "$LAUNCH_DIR/load.out")
if [[ "${inserted:-0}" != "$RECORDCOUNT" ]]; then
  log "Load inserted ${inserted:-0} of $RECORDCOUNT records, see load.out/load.err"
  exit 1
fi
log "Loaded $inserted records in $(( SECONDS - load_start ))s"

if [[ "$DB" == cassandra ]]; then
  db "nodetool flush ycsb"
else
  db "mongosh --quiet --host 127.0.0.1 --eval 'db.adminCommand({fsync: 1})'" >/dev/null
fi
wait_compaction
echo "on_disk_bytes=$(db "sudo du -sb $DATA_DIR" | cut -f1)" >> "$LAUNCH_DIR/env.txt"

log "Cold restart of $DB"
cold_restart

# The per-run warm-up is too short to refill the cache after a cold restart (pilot:
# read-heavy at 1 thread kept climbing for its whole measured run). Read-only, so the
# data is unchanged.
if (( INITIAL_WARMUP_S > 0 )); then
  log "Cache warm-up: read-only, $LOAD_THREADS threads, ${INITIAL_WARMUP_S}s"
  ycsb run read-heavy "$LOAD_THREADS" "$LAUNCH_DIR/cache-warmup" -p maxexecutiontime="$INITIAL_WARMUP_S" \
    -p readproportion=1 -p updateproportion=0 || log "WARN: cache warm-up exited non-zero"
fi

# --- Runs ----------------------------------------------------------------------
i=0
for w in $WORKLOADS; do
  for t in $(threads_for "$w"); do
    i=$((i + 1))
    dir="$LAUNCH_DIR/$w/t$t"
    mkdir -p "$dir"
    log "[$i/$n_runs] $w, $t threads: warm-up ${WARMUP_S}s"
    ycsb run "$w" "$t" "$dir/warmup" -p maxexecutiontime="$WARMUP_S" || log "WARN: warm-up exited non-zero"

    log "[$i/$n_runs] $w, $t threads: measure ${MEASURE_S}s"
    start_sar "$dir"
    start_epoch=$(date +%s)
    rc=0
    ycsb run "$w" "$t" "$dir/run" -p maxexecutiontime="$MEASURE_S" || rc=$?
    end_epoch=$(date +%s)
    stop_sar "$dir"
    (( rc == 0 )) || log "WARN: measured run exited with $rc, see $dir/run.err"

    printf '{"setup": "%s", "db": "%s", "server_size": "%s", "launch": %s, "workload": "%s", "threads": %s, "recordcount": %s, "warmup_s": %s, "measure_s": %s, "start_epoch": %s, "end_epoch": %s, "exit_code": %s}\n' \
      "$SETUP" "$DB" "$SERVER_SIZE" "$LAUNCH" "$w" "$t" "$RECORDCOUNT" "$WARMUP_S" "$MEASURE_S" \
      "$start_epoch" "$end_epoch" "$rc" > "$dir/run.json"
    log "[$i/$n_runs] $(grep -F '[OVERALL], Throughput' "$dir/run.out" || echo 'no throughput line')"
  done
  wait_compaction
done

echo "end_utc=$(date -u +%FT%TZ)" >> "$LAUNCH_DIR/env.txt"
log "Launch complete: $LAUNCH_DIR"
python3 "$PIPELINE_DIR/parse_results.py" "$LAUNCH_DIR" || log "WARN: parse_results.py failed"
