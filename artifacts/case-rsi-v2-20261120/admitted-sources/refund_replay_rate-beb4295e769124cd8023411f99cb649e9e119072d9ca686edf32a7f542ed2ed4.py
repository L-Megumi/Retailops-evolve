def compute_metric(orders, refunds):
    total_refund_rows = len(refunds)
    seen = {}
    duplicate_rows = 0
    for row in refunds:
        refund_id = row.get('refund_id')
        if refund_id in seen:
            duplicate_rows = duplicate_rows + 1
        else:
            seen.setdefault(refund_id, 1)
    if total_refund_rows == 0:
        replay_rate_bps = 0
    else:
        replay_rate_bps = (duplicate_rows * 10000) // total_refund_rows
    data = {
        'total_refund_rows': total_refund_rows,
        'duplicate_rows': duplicate_rows,
        'replay_rate_bps': replay_rate_bps
    }
    evidence = {
        'total_refund_rows': total_refund_rows,
        'duplicate_rows': duplicate_rows,
        'replay_rate_bps': replay_rate_bps
    }
    return {'metric': 'refund_replay_rate', 'data': data, 'evidence': evidence}
