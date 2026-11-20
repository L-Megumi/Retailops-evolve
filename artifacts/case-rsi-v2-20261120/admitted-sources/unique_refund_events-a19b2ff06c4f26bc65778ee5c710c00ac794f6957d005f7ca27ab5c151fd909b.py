def compute_metric(orders, refunds):
    seen = {}
    for row in refunds:
        rid = row.get('refund_id')
        if rid not in seen:
            seen[rid] = 1
    count = len(seen)
    data = {'unique_refund_events': count}
    evidence = {'refund_rows': len(refunds), 'distinct_refund_ids': count}
    return {'metric': 'unique_refund_events', 'data': data, 'evidence': evidence}
