-- Business logic of the daily_revenue data product: a plain SELECT, nothing else.
-- contractctl wraps it with the schema, expectations, row filter, comments and
-- properties promised in daily_revenue.odcs.yaml. Read only from declared inputs.
SELECT
  CAST(order_ts AS DATE)                                                    AS order_date,
  country_code,
  COUNT(*)                                                                  AS orders_count,
  COUNT_IF(order_status = 'CANCELLED')                                      AS cancelled_count,
  CAST(SUM(CASE WHEN order_status <> 'CANCELLED' THEN amount ELSE 0 END)
       AS DECIMAL(18,2))                                                    AS revenue_eur,
  CAST(SUM(CASE WHEN order_status <> 'CANCELLED' THEN amount END)
       / NULLIF(COUNT_IF(order_status <> 'CANCELLED'), 0) AS DECIMAL(18,2)) AS avg_order_value_eur,
  MAX(ingested_at)                                                          AS latest_ingested_at
FROM silver.orders
GROUP BY CAST(order_ts AS DATE), country_code
