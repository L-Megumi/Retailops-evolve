"""Validated, shop-scoped input records for read-only after-sales review."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

CHECKS = frozenset({"records", "amount", "freight", "materials"})
REFUND_STATUSES = frozenset({"requested", "approved", "succeeded", "failed", "cancelled"})


def text(value, name, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be a non-empty string of at most {limit} characters")
    return value.strip()


def money(value, name):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"{name} must be a decimal string or integer")
    try:
        result = Decimal(value)
        if not result.is_finite() or result < 0 or result.as_tuple().exponent < -2 or result > Decimal('1000000000000'):
            raise ValueError(f"invalid {name}")
        return result
    except InvalidOperation as exc:
        raise ValueError(f"invalid {name}") from exc


def iso_date(value, name):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(f"{name} must be YYYY-MM-DD")
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError(f"invalid {name}")
    return value


def key(row):
    return (row["platform"], row["shop_id"], row["order_id"])


@dataclass(frozen=True)
class CaseBook:
    cases: tuple[dict, ...]
    orders: tuple[dict, ...]
    refunds: tuple[dict, ...]
    evidence: tuple[dict, ...]
    dataset_kind: str
    content_hash: str

    @classmethod
    def from_path(cls, path):
        with Path(path).open('rb') as handle:
            raw = handle.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("casebook exceeds 4 MiB")
        try:
            payload = json.loads(raw.decode('utf-8-sig'))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("casebook must be UTF-8 JSON") from exc
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict) or type(payload.get('schema_version')) is not int or payload['schema_version'] != 1:
            raise ValueError("casebook schema_version must be 1")
        if payload.get('dataset_kind') not in {'synthetic', 'user_provided', 'public_case_adaptation'}:
            raise ValueError("unsupported dataset_kind")
        limits = {'cases': 1000, 'orders': 10000, 'refunds': 20000, 'evidence': 10000}
        tables = {}
        evidence_ids = set()
        case_ids = set()
        for table, limit in limits.items():
            values = payload.get(table, [])
            if not isinstance(values, list) or len(values) > limit or (table == 'cases' and not values):
                raise ValueError(f"invalid {table} table")
            rows = []
            for item in values:
                if not isinstance(item, dict):
                    raise ValueError(f"{table} rows must be objects")
                row = dict(item)
                if table != 'evidence':
                    for field in ('platform', 'shop_id', 'order_id'):
                        row[field] = text(row.get(field), field)
                if table == 'cases':
                    cid = row['case_id'] = text(row.get('case_id'), 'case_id')
                    if cid in case_ids:
                        raise ValueError("duplicate case_id")
                    case_ids.add(cid)
                    scope = row.get('business_scope', 'platform')
                    if scope not in {'platform', 'marketplace', 'self_operated'}:
                        raise ValueError("invalid business_scope")
                    row['business_scope'] = scope
                    checks = row.get('checks', sorted(CHECKS))
                    if not isinstance(checks, list) or not checks or len(checks) > 12:
                        raise ValueError("checks must contain 1-12 check names")
                    row['checks'] = list(dict.fromkeys(text(c, 'check', 80) for c in checks))
                    if row.get('reason') not in {None, 'quality', 'personal', 'description_mismatch', 'wrong_item', 'other'}:
                        raise ValueError("invalid reason")
                    for field in ('application_date', 'policy_as_of'):
                        if row.get(field) is not None:
                            iso_date(row[field], field)
                    if row.get('request_type', 'return') not in {'return', 'exchange', 'repair', 'refund_only'}:
                        raise ValueError("invalid request_type")
                    if row.get('proposed_freight_party') not in {None, 'buyer', 'merchant', 'jd'}:
                        raise ValueError('invalid proposed_freight_party')
                    parameters = row.get('review_parameters', {})
                    if not isinstance(parameters, dict) or set(parameters) - {'window_days','event_count_threshold','average_amount_threshold','replay_rate_threshold_bps'}:
                        raise ValueError('invalid review_parameters')
                    for name,value in parameters.items():
                        if name == 'average_amount_threshold':
                            parameters = dict(parameters)
                            parameters[name] = str(money(value,name))
                        elif type(value) is not int or not 0 <= value <= {'window_days':365,'event_count_threshold':20000,'replay_rate_threshold_bps':10000}[name] or (name=='window_days' and value==0):
                            raise ValueError('invalid review parameter: '+name)
                    row['review_parameters'] = parameters
                else:
                    eid = row['evidence_id'] = text(row.get('evidence_id'), 'evidence_id')
                    if eid in evidence_ids:
                        raise ValueError("duplicate evidence_id")
                    evidence_ids.add(eid)
                    if table == 'orders':
                        if row.get('paid_amount') is not None:
                            row['paid_amount'] = str(money(row['paid_amount'], 'paid_amount'))
                        if row.get('received_date') is not None:
                            iso_date(row['received_date'], 'received_date')
                        for field in ('supports_seven_day', 'shipping_included', 'has_gifts', 'has_paper_invoice'):
                            if row.get(field) is not None and type(row[field]) is not bool:
                                raise ValueError(f"{field} must be boolean or null")
                        if row.get('goods_condition') not in {None, 'intact', 'damaged', 'unknown'}:
                            raise ValueError('invalid goods_condition')
                    elif table == 'refunds':
                        row['refund_id'] = text(row.get('refund_id'), 'refund_id')
                        row['refund_amount'] = str(money(row.get('refund_amount'), 'refund_amount'))
                        if Decimal(row['refund_amount']) <= 0 or row.get('status') not in REFUND_STATUSES:
                            raise ValueError("invalid refund amount or status")
                        iso_date(row.get('refund_date'), 'refund_date')
                        if row.get('refund_transaction_id') is not None:
                            row['refund_transaction_id'] = text(row['refund_transaction_id'], 'refund_transaction_id')
                    else:
                        row['case_id'] = text(row.get('case_id'), 'case_id')
                        row['kind'] = text(row.get('kind'), 'evidence kind', 80)
                        if row.get('status', 'present') not in {'present', 'confirmed', 'rejected'}:
                            raise ValueError('invalid evidence status')
                        if 'text' in row:
                            row['text'] = text(row['text'], 'evidence text', 6000)
                rows.append(row)
            tables[table] = tuple(rows)
        if any(row['case_id'] not in case_ids for row in tables['evidence']):
            raise ValueError('evidence references an unknown case_id')
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        return cls(**tables, dataset_kind=payload['dataset_kind'], content_hash=hashlib.sha256(canonical.encode()).hexdigest())
