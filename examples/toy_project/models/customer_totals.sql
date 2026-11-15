SELECT
    c.name,
    SUM(o.amount) AS total
FROM orders AS o
JOIN customers AS c ON o.customer_id = c.id
GROUP BY c.name
