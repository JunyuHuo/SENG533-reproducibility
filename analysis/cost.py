#!/usr/bin/env python3
"""Derive combined VM hourly compute rates from sanitized OCI usage exports."""
import csv
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {'base': 'reports_cost_baseline.csv', 'upgraded': 'reports_cost_upgraded.csv'}


def compute_rates():
    result = {}
    for size, name in FILES.items():
        with (ROOT / 'data/cost' / name).open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        groups = defaultdict(list)
        for row in rows:
            quantity, price, cost = (Decimal(row[k]) for k in (
                'usage/billedQuantity', 'cost/unitPrice', 'cost/myCost'))
            if quantity * price != cost:
                raise ValueError(f'{name}: usage x unit price does not equal cost')
            if row['cost/currencyCode'] != 'CAD' or row['product/region'] != 'ca-toronto-1':
                raise ValueError(f'{name}: unexpected currency or region')
            if row['product/service'] == 'COMPUTE':
                groups[row['lineItem/intervalUsageStart'], row['lineItem/intervalUsageEnd']].append(row)
        accepted, excluded, rates = [], [], set()
        for (start, end), group in sorted(groups.items()):
            by_vm = defaultdict(dict)
            for row in group:
                unit = row['cost/skuUnitDescription']
                if unit not in ('OCPU Per Hour', 'Gigabyte Per Hour'):
                    raise ValueError(f'{name}: unexpected compute billing unit')
                if unit in by_vm[row['resource_alias']]:
                    raise ValueError(f'{name}: duplicate VM/unit in interval')
                by_vm[row['resource_alias']][unit] = Decimal(row['usage/billedQuantity'])
            seconds = (datetime.fromisoformat(end.replace('Z', '+00:00')) -
                       datetime.fromisoformat(start.replace('Z', '+00:00'))).total_seconds()
            full = seconds == 3600 and len(by_vm) == 2 and all(
                vm.get('OCPU Per Hour') == Decimal(2) and 'Gigabyte Per Hour' in vm
                for vm in by_vm.values())
            if not full:
                excluded.append(start)
                continue
            memory = sorted(vm['Gigabyte Per Hour'] for vm in by_vm.values())
            if memory != ([Decimal(16), Decimal(16)] if size == 'base' else [Decimal(16), Decimal(32)]):
                raise ValueError(f'{name}: full-hour allocation does not match experiment')
            hourly = sum(Decimal(row['cost/myCost']) for row in group)
            rates.add(hourly)
            accepted.append(start)
        if len(rates) != 1:
            raise ValueError(f'{name}: no unique full-hour compute rate')
        result[size] = dict(cad_per_hour=str(rates.pop()),
            complete_hour_intervals=accepted, excluded_compute_intervals=excluded,
            source='data/cost/' + name)
    return result


def hourly_rates():
    return {size: float(info['cad_per_hour']) for size, info in compute_rates().items()}


if __name__ == '__main__':
    print(json.dumps(compute_rates(), indent=2))
