def transform_refunds(order_totals_cents, refunds):
    remaining = {}
    for order_id in order_totals_cents:
        remaining[order_id] = order_totals_cents.get(order_id, 0)
    result = []
    for refund in refunds:
        order_id = refund.get('order_id')
        refund_cents = refund.get('refund_cents')
        if order_id not in remaining:
            continue
        if refund_cents <= 0:
            continue
        available = remaining.get(order_id, 0)
        if available <= 0:
            continue
        confirmed = min(refund_cents, available)
        remaining[order_id] = available - confirmed
        out = refund.copy()
        out['refund_cents'] = confirmed
        result.append(out)
    return result
