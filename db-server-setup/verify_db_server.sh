#!/usr/bin/env bash
set -Eeuo pipefail

DB_PRIVATE_IP="${DB_PRIVATE_IP:?Set the database VM private IP}"
LOAD_GENERATOR_IP="${LOAD_GENERATOR_IP:?Set the load-generator VM private IP}"

echo "Package versions"
dpkg-query -W -f='${binary:Package}\t${Version}\n' mongodb-org mongodb-org-server mongodb-mongosh cassandra sysstat

echo "Service state"
systemctl is-active seng533-db-firewall sysstat
systemctl is-active mongod || true
systemctl is-active cassandra || true

echo "Configured endpoints"
grep -E '^[[:space:]]*(bindIp|port):' /etc/mongod.conf
grep -E '^(listen_address|rpc_address|broadcast_rpc_address|native_transport_port):' /etc/cassandra/cassandra.yaml

echo "Firewall"
nft list table inet seng533_db_filter
iptables -S INPUT | grep -E -- '--dport (27017|9042)' || true

echo "Expected clients"
printf 'MongoDB:  mongodb://%s:27017/ycsb?w=1\n' "$DB_PRIVATE_IP"
printf 'Cassandra: %s:9042 (keyspace ycsb, table usertable)\n' "$DB_PRIVATE_IP"
printf 'Allowed source: %s\n' "$LOAD_GENERATOR_IP"
