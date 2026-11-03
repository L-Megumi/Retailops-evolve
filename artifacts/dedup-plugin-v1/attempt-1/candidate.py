def transform_refunds(order_totals_cents, refunds):
    seen = {}
    result = []
    for row in refunds:
        refund_id = row.get('refund_id')
        if refund_id not in seen:
            seen[refund_id] = True
            result.append(row.copy())
    return result
