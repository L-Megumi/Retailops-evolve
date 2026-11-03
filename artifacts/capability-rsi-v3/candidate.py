def compute_metric(orders, refunds):
    if refunds == None:
        refunds = []
    total_refund_rows = len(refunds)
    duplicate_rows = 0
    seen = {}
    for row in refunds:
        refund_id = row.get('refund_id')
        key = str(refund_id)
        if key in seen:
            duplicate_rows = duplicate_rows + 1
        else:
            seen[key] = 1
    replay_rate_bps = 0
    if total_refund_rows > 0:
        replay_rate_bps = int(duplicate_rows * 10000 / total_refund_rows)
    return {'metric': 'refund_replay_rate', 'data': {'total_refund_rows': int(total_refund_rows), 'duplicate_rows': int(duplicate_rows), 'replay_rate_bps': int(replay_rate_bps)}, 'evidence': {'refund_id_field': 'refund_id', 'duplicate_rule': 'after_first_occurrence', 'formula': 'floor(duplicate_rows * 10000 / total_refund_rows)'}}
