"""Case-to-metric adapters. Only executable tool gaps can enter bounded RSI."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path

from .case_data import key
from .evolution import CapabilityEvolver
from .persistence import atomic_json, file_lock
from .plugin_manager import PluginManager


@dataclass(frozen=True)
class CaseCapability:
    name: str
    metric: str
    aliases: tuple[str, ...]
    parameters: tuple[str, ...]
    description: str


CAPABILITIES = {
    c.name: c for c in (
        CaseCapability('refund_velocity', 'unique_refund_events', ('退款频次', '退款频率', '短期退款次数', 'refund_velocity'),
                       ('window_days', 'event_count_threshold'),
                       'Count unique succeeded refund events within the case reference window; alert only when count exceeds an explicit threshold.'),
        CaseCapability('refund_average', 'average_refund_amount', ('平均成功退款金额', '退款均额', 'refund_average'),
                       ('average_amount_threshold',),
                       'Average succeeded unique refund amounts in cents; compare with the explicit analyst threshold, not a platform rule.'),
        CaseCapability('refund_replay', 'refund_replay_rate', ('退款记录重放率', '重复行比例', 'refund_replay'),
                       ('replay_rate_threshold_bps',),
                       'Duplicate refund ID row share in raw scoped refund export; report record replay, never repeated real payments.'),
    )
}


class BudgetModel:
    """One task-wide code-generation budget, independent of report drafting."""
    def __init__(self, model, limit):
        self.model, self.limit, self.calls = model, limit, 0
    def complete_json(self, **kwargs):
        if self.calls >= self.limit:
            raise RuntimeError('case evolution model budget exhausted')
        self.calls += 1
        return self.model.complete_json(**kwargs)


class CaseCapabilityRuntime:
    def __init__(self, manager, *, model=None, artifact_root=Path('runs/case-evolution'),
                 allow_evolution=False, max_attempts=2, max_generation_calls=6):
        if type(max_generation_calls) is not int or not 0 <= max_generation_calls <= 12:
            raise ValueError('max_generation_calls must be 0-12')
        self.manager = manager
        self.generator = BudgetModel(model, max_generation_calls) if model is not None else None
        self.evolver = CapabilityEvolver(self.generator, artifact_root, max_attempts) if allow_evolution and self.generator else None
        self.failed_metrics = set()

    @classmethod
    def from_path(cls, path, **kwargs):
        path = Path(path)
        with file_lock(path):
            if not path.exists():
                atomic_json(path, {'schema_version': 2, 'plugins': []})
        return cls(PluginManager.from_manifest(path), **kwargs)

    @property
    def generation_calls(self):
        return self.generator.calls if self.generator else 0

    def begin_task(self):
        self.failed_metrics.clear()
        if self.generator:
            self.generator.calls = 0

    def available(self, check):
        definition = CAPABILITIES.get(check)
        return bool(definition and any(r.status == 'active' and r.spec.output_name == definition.metric for r in self.manager.records))

    @staticmethod
    def prepare(check, case, orders, refunds, all_refund_ids, all_transactions):
        definition = CAPABILITIES[check]
        parameters = case.get('review_parameters', {})
        missing = [p for p in definition.parameters if p not in parameters]
        if check == 'refund_velocity' and not case.get('application_date'):
            missing.append('application_date')
        if len(orders) != 1:
            missing.append('unique_order_record')
        if missing:
            return None, sorted(set(missing))
        if check == 'refund_velocity' and date.fromisoformat(case['application_date']).toordinal() < parameters['window_days']:
            return None, ['reference_window']
        rows = []
        seen = set()
        for row in refunds:
            group = all_refund_ids[(row['platform'], row['shop_id'], row['refund_id'])]
            signatures = {(r['order_id'], str(Decimal(r['refund_amount']).normalize()), r['refund_date'],
                           r['status'], r.get('refund_transaction_id')) for r in group}
            if len(signatures) != 1:
                return None, ['refund_record_conflict']
            tid = row.get('refund_transaction_id')
            if tid and row['status'] == 'succeeded':
                related = all_transactions[(row['platform'], row['shop_id'], tid)]
                if len({(r['order_id'], r['refund_id']) for r in related}) > 1:
                    return None, ['refund_transaction_reused']
            include = True
            if check != 'refund_replay':
                include = row['status'] == 'succeeded' and row['refund_id'] not in seen
                seen.add(row['refund_id'])
            if include and check == 'refund_velocity':
                end = date.fromisoformat(case['application_date'])
                start = end - timedelta(days=parameters['window_days'] - 1)
                include = start <= date.fromisoformat(row['refund_date']) <= end
            if include:
                rows.append(row)
        # These three trusted definitions consume refund rows only. Do not invent
        # an order gross amount when the original payment amount is unavailable.
        order_rows = []
        metric_rows = [{'refund_id': r['refund_id'], 'order_id': r['order_id'], 'refund_date': r['refund_date'],
                        'refund_cents': int(Decimal(r['refund_amount']) * 100)} for r in rows]
        return (order_rows, metric_rows, [orders[0]['evidence_id']] + [r['evidence_id'] for r in rows]), []

    def execute(self, check, case, prepared, emit, *, allow_generation=True):
        definition = CAPABILITIES[check]
        receipt = None
        if not self.available(check):
            if self.evolver is None or not allow_generation:
                raise RuntimeError('capability_not_installed')
            if definition.metric in self.failed_metrics:
                raise RuntimeError('capability_generation_already_failed_in_this_task')
            emit('capability_gap', case_id=case['case_id'], check=check, metric=definition.metric)
            try:
                # Generation sees the developer-owned definition, no merchant rows,
                # evidence text, QA answers or arbitrary case attachment commands.
                receipt = self.evolver.ensure(self.manager, definition.metric,
                    'Provide the trusted read-only metric for case review: ' + definition.description)
            except (ValueError, RuntimeError) as exc:
                self.failed_metrics.add(definition.metric)
                emit('capability_rejected', case_id=case['case_id'], check=check, reason=type(exc).__name__)
                raise
            emit('capability_admitted', case_id=case['case_id'], check=check, receipt=receipt)
            emit('case_replan', case_id=case['case_id'], check=check, reason='verified_capability_available')
        else:
            emit('capability_reused', case_id=case['case_id'], check=check, metric=definition.metric)
        order_rows, refund_rows, evidence_ids = prepared
        result = self.manager.execute_metric_rows(definition.metric, order_rows, refund_rows)
        return result, evidence_ids, receipt
