def compute_metric(orders, refunds):
    total_refund_rows = len(refunds)
    total_cents = 0
    for row in refunds:
        total_cents = total_cents + int(row.get('refund_cents', 0))
    if total_refund_rows == 0:
        average_refund_cents = 0
    else:
        average_refund_cents = total_cents // total_refund_rows
    data = {'total_refund_rows': total_refund_rows, 'average_refund_cents': average_refund_cents}
    evidence = {'total_cents': total_cents, 'refund_rows': total_refund_rows}
    return {'metric': 'average_refund_amount', 'data': data, 'evidence': evidence}
