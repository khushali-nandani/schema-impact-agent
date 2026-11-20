SELECT
    name,
    total
FROM customer_totals
WHERE total > 1000
ORDER BY total DESC
