# Weekly Pack Fulfillment

## Manual fulfillment (existing)

```bash
cd /root/rareearth-sentinel && PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/fulfill_pack_order.py --order-id <ORDER_ID>
```

## Auto fulfillment (recommended)

To auto-trigger weekly pack delivery after order confirmation, run the pay-confirm service with:

```bash
/root/rareearth-sentinel/scripts/pay_confirm_server_auto.py
```

This wrapper monkey-patches `_issue_unlock` and will:
- detect `weekly_pack` from pay status / memo
- execute `scripts/fulfill_pack_order.py --order-id <ORDER_ID>` automatically
- return `delivery_url` as unlock URL when available
