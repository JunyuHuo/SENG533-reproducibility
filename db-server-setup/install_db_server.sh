#!/usr/bin/env bash
set -Eeuo pipefail

MONGO_SERIES="${MONGO_SERIES:-7.0}"
CASSANDRA_SERIES="${CASSANDRA_SERIES:-41x}"
DB_PRIVATE_IP="${DB_PRIVATE_IP:-$(hostname -I | awk '{print $1}')}"
LOAD_GENERATOR_IP="${LOAD_GENERATOR_IP:?Set the load-generator VM private IP}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo DB_PRIVATE_IP=... LOAD_GENERATOR_IP=... $0" >&2
  exit 1
fi

source /etc/os-release
if [[ "${ID}" != "ubuntu" || "${VERSION_ID}" != "22.04" ]]; then
  echo "This setup is pinned to Ubuntu 22.04; found ${PRETTY_NAME}." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg openjdk-11-jre-headless sysstat nftables

install -d -m 0755 /etc/apt/keyrings
curl -fsSL "https://www.mongodb.org/static/pgp/server-${MONGO_SERIES}.asc" \
  | gpg --dearmor --yes -o "/etc/apt/keyrings/mongodb-server-${MONGO_SERIES}.gpg"
cat > "/etc/apt/sources.list.d/mongodb-org-${MONGO_SERIES}.list" <<EOF
deb [arch=amd64 signed-by=/etc/apt/keyrings/mongodb-server-${MONGO_SERIES}.gpg] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/${MONGO_SERIES} multiverse
EOF

curl -fsSL https://downloads.apache.org/cassandra/KEYS \
  | gpg --dearmor --yes -o /etc/apt/keyrings/apache-cassandra.gpg
cat > /etc/apt/sources.list.d/cassandra.sources.list <<EOF
deb [signed-by=/etc/apt/keyrings/apache-cassandra.gpg] https://debian.cassandra.apache.org ${CASSANDRA_SERIES} main
EOF

apt-get update
apt-get install -y mongodb-org cassandra
apt-mark hold mongodb-org mongodb-org-database mongodb-org-server mongodb-mongosh cassandra >/dev/null

systemctl stop mongod cassandra 2>/dev/null || true
systemctl disable mongod cassandra >/dev/null 2>&1 || true

sed -ri "s|^[[:space:]]*bindIp:.*|  bindIp: 127.0.0.1,${DB_PRIVATE_IP}|" /etc/mongod.conf
sed -ri "s|^[[:space:]]*port:.*|  port: 27017|" /etc/mongod.conf

python3 - "$DB_PRIVATE_IP" <<'PY'
from pathlib import Path
import re
import sys

path = Path('/etc/cassandra/cassandra.yaml')
text = path.read_text()
private_ip = sys.argv[1]

def replace_scalar(key, value):
    global text
    pattern = rf'(?m)^(\s*)#?\s*{re.escape(key)}:\s*.*$'
    replacement = rf'\1{key}: {value}'
    text, count = re.subn(pattern, replacement, text, count=1)
    if count != 1:
        raise SystemExit(f'Could not update {key} in {path}')

replace_scalar('cluster_name', "'SENG533-NoSQL-Benchmark'")
replace_scalar('listen_address', private_ip)
replace_scalar('rpc_address', '0.0.0.0')
replace_scalar('broadcast_rpc_address', private_ip)
replace_scalar('endpoint_snitch', 'GossipingPropertyFileSnitch')
replace_scalar('native_transport_port', '9042')
text, count = re.subn(
    r'(?m)^(\s*- seeds:)\s*"[^"]*"',
    rf'\1 "{private_ip}:7000"',
    text,
    count=1,
)
if count != 1:
    raise SystemExit(f'Could not update seed address in {path}')
path.write_text(text)
PY

cat > /etc/nftables.d-seng533-db.nft <<EOF
table inet seng533_db_filter {
  chain input {
    type filter hook input priority -10; policy accept;
    ip saddr ${LOAD_GENERATOR_IP} tcp dport { 27017, 9042 } accept
    ip saddr ${DB_PRIVATE_IP} tcp dport { 27017, 9042 } accept
    ip saddr 127.0.0.1 tcp dport { 27017, 9042 } accept
    tcp dport { 27017, 9042 } drop
  }
}
EOF

cat > /usr/local/sbin/seng533-db-firewall <<EOF
#!/bin/sh
set -eu
case "\${1:-start}" in
  start)
    /usr/sbin/nft delete table inet seng533_db_filter 2>/dev/null || true
    /usr/sbin/nft -f /etc/nftables.d-seng533-db.nft
    /usr/sbin/iptables -C INPUT -p tcp -s ${LOAD_GENERATOR_IP} --dport 27017 -j ACCEPT 2>/dev/null || \
      /usr/sbin/iptables -I INPUT 1 -p tcp -s ${LOAD_GENERATOR_IP} --dport 27017 -j ACCEPT
    /usr/sbin/iptables -C INPUT -p tcp -s ${LOAD_GENERATOR_IP} --dport 9042 -j ACCEPT 2>/dev/null || \
      /usr/sbin/iptables -I INPUT 1 -p tcp -s ${LOAD_GENERATOR_IP} --dport 9042 -j ACCEPT
    ;;
  stop)
    /usr/sbin/iptables -D INPUT -p tcp -s ${LOAD_GENERATOR_IP} --dport 27017 -j ACCEPT 2>/dev/null || true
    /usr/sbin/iptables -D INPUT -p tcp -s ${LOAD_GENERATOR_IP} --dport 9042 -j ACCEPT 2>/dev/null || true
    /usr/sbin/nft delete table inet seng533_db_filter 2>/dev/null || true
    ;;
  *)
    echo "Usage: \$0 {start|stop}" >&2
    exit 2
    ;;
esac
EOF
chmod 0755 /usr/local/sbin/seng533-db-firewall

cat > /etc/systemd/system/seng533-db-firewall.service <<'EOF'
[Unit]
Description=SENG533 database-port firewall
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/seng533-db-firewall start
ExecStop=/usr/local/sbin/seng533-db-firewall stop

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable seng533-db-firewall.service
systemctl restart seng533-db-firewall.service

sed -ri 's/^ENABLED=.*/ENABLED="true"/' /etc/default/sysstat
systemctl enable --now sysstat

systemctl start cassandra
for _ in $(seq 1 90); do
  if cqlsh 127.0.0.1 9042 -e 'DESCRIBE CLUSTER' >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
if ! cqlsh 127.0.0.1 9042 -e 'DESCRIBE CLUSTER' >/dev/null 2>&1; then
  journalctl -u cassandra -n 100 --no-pager >&2
  exit 1
fi

cqlsh 127.0.0.1 9042 <<'CQL'
CREATE KEYSPACE IF NOT EXISTS ycsb
WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1};

CREATE TABLE IF NOT EXISTS ycsb.usertable (
  y_id varchar PRIMARY KEY,
  field0 varchar,
  field1 varchar,
  field2 varchar,
  field3 varchar,
  field4 varchar,
  field5 varchar,
  field6 varchar,
  field7 varchar,
  field8 varchar,
  field9 varchar
);
CQL

systemctl stop cassandra
systemctl start mongod
for _ in $(seq 1 60); do
  if mongosh --quiet --host 127.0.0.1 --port 27017 --eval 'db.runCommand({ping:1}).ok' 2>/dev/null | grep -qx 1; then
    break
  fi
  sleep 1
done
if ! mongosh --quiet --host 127.0.0.1 --port 27017 --eval 'db.runCommand({ping:1}).ok' 2>/dev/null | grep -qx 1; then
  journalctl -u mongod -n 100 --no-pager >&2
  exit 1
fi
mongosh --quiet --host 127.0.0.1 --port 27017 ycsb --eval \
  'if (!db.getCollectionNames().includes("usertable")) db.createCollection("usertable"); db.runCommand({ping:1})' >/dev/null

cat > /etc/seng533-db.env <<EOF
DB_PRIVATE_IP=${DB_PRIVATE_IP}
LOAD_GENERATOR_IP=${LOAD_GENERATOR_IP}
MONGODB_PORT=27017
CASSANDRA_PORT=9042
EOF
chmod 0644 /etc/seng533-db.env

cat > /etc/systemd/system/seng533-db-exclusive.conf <<'EOF'
# MongoDB and Cassandra are intentionally not enabled at boot.
# Use /usr/local/sbin/seng533-switch-db to guarantee mutual exclusion.
EOF

echo "Installed versions:"
mongod --version | head -n 1
cassandra -v
echo "MongoDB is active on ${DB_PRIVATE_IP}:27017; Cassandra is installed and stopped."
