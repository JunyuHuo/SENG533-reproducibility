#!/usr/bin/env python3
"""Validate checksums, rebuild both CSVs, and check every measurement and run key."""
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

from cost import hourly_rates

ROOT=Path(__file__).resolve().parents[1]

def read(path):
    with path.open(newline='') as f: return list(csv.DictReader(f))

def canonical(rows):
    for r in rows:
        if 'run_dir' in r: r['run_dir']='oci-single/'+r['run_dir'].split('oci-single/')[-1]
    return rows

def main():
    for line in (ROOT/'SHA256SUMS').read_text().splitlines():
        digest,name=line.split('  ',1)
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:
            raise SystemExit(f'Checksum mismatch: {name}')
    rates = hourly_rates()
    if rates != {'base': 0.2726048, 'upgraded': 0.3040592}:
        raise SystemExit(f'Historical hourly rates changed: {rates}')
    print('PASS: OCI line-item arithmetic and complete-hour compute rates')
    actual=read(ROOT/'data/runs.csv')
    keys=[(r['db'],r['server_size'],int(r['launch']),r['workload'],int(r['threads'])) for r in actual]
    expected=set()
    for db in ['cassandra','mongo']:
        for launch in [1,2,3]:
            for w in ['read-heavy','mixed','write-heavy']:
                for t in [1,4,8,16,32,64,128]: expected.add((db,'base',launch,w,t))
        for w in ['read-heavy','write-heavy']:
            for t in [1,4,8,16,32,64,128]: expected.add((db,'upgraded',1,w,t))
            selected={('cassandra','read-heavy'):64,('cassandra','write-heavy'):128,
                      ('mongo','read-heavy'):64,('mongo','write-heavy'):1}[db,w]
            for launch in [2,3]: expected.add((db,'upgraded',launch,w,selected))
    if len(keys)!=162 or set(keys)!=expected: raise SystemExit('Missing, duplicate, or unexpected experiment configuration')
    with tempfile.TemporaryDirectory(prefix='seng533-verify-') as d:
        out=Path(d)
        with tarfile.open(ROOT/'data/measurements.tar.gz') as t:
            for m in t.getmembers():
                if m.issym() or m.islnk() or not (m.isfile() or m.isdir()): raise SystemExit('Unsafe archive member')
                if m.name.startswith('/') or '..' in Path(m.name).parts: raise SystemExit('Unsafe archive path')
                if m.isfile():
                    p=out/m.name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(t.extractfile(m).read())
        if len(list(out.rglob('run.json')))!=162: raise SystemExit('Archive missing run metadata')
        subprocess.run([sys.executable,str(ROOT/'pipeline/parse_results.py'),str(out/'oci-single'),'-o',str(out)],
                       env={**os.environ,'TZ':'UTC'},check=True,capture_output=True,text=True)
        for name,n in [('runs.csv',162),('timeseries.csv',5184)]:
            reference=canonical(read(ROOT/'data'/name)); regenerated=canonical(read(out/name))
            if len(reference)!=n or reference!=regenerated: raise SystemExit(f'Regenerated data differ: {name}')
            print(f'PASS: {name}, {n} rows, all fields match after path normalization')
        for p in [ROOT/'data/runs.csv',out/'runs.csv']:
            result=subprocess.check_output([sys.executable,str(ROOT/'analysis/summarize.py'),str(p)],text=True)
            if p==ROOT/'data/runs.csv': first=json.loads(result)
            elif first!=json.loads(result): raise SystemExit('Summary differs')
    print('PASS: checksums, complete experiment matrix, 162 archived runs, and numerical summary')

if __name__=='__main__': main()
