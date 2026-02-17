# RareEarth Sentinel

新增：周包交付辅助脚本（v2.9 临时闭环）

当订单为 weekly_pack 且已收款后，可执行：

```bash
cd /root/rareearth-sentinel && PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/fulfill_pack_order.py --order-id <ORDER_ID>
```

脚本会：
- 读取 `data/products/packs.json` 的 `pack_id -> report_ids`
- 为每篇生成解锁链接
- 生成交付页：`/{lang}/pack/unlock/{order_id}.html`
- 回写 `pay/status/{order_id}.json` 与 `orders.jsonl` 的结构化字段：
  - `product_type`
  - `sku_id`
  - `channel`
  - `pack_id`
  - `fulfillment_status`
  - `delivery_url`
