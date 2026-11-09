-- Aggregate semantic duplicate components across different applications.
-- The semantic worker must populate duplicate_edges for pairs whose cosine
-- similarity passed its threshold. Each edge is only valid for a five-minute
-- timestamp window; the query rechecks that invariant.
--
-- :since is an ISO-8601 UTC value, normally datetime('now', '-24 hours').
-- :similarity_threshold prevents old/low-confidence edges from joining a thread.

WITH RECURSIVE
recent AS (
    SELECT n.notification_id, n.source_id, s.name AS source_app,
           n.sender, n.title, n.content, n.received_at, n.category_id
    FROM notifications AS n
    JOIN sources AS s ON s.source_id = n.source_id
    WHERE n.received_at >= :since
),
eligible_edges AS (
    SELECT e.source_notification_id AS left_id,
           e.target_notification_id AS right_id,
           e.similarity
    FROM duplicate_edges AS e
    JOIN recent AS l ON l.notification_id = e.source_notification_id
    JOIN recent AS r ON r.notification_id = e.target_notification_id
    WHERE e.similarity >= :similarity_threshold
      AND abs(unixepoch(l.received_at) - unixepoch(r.received_at)) <= 300
),
links(node_id, linked_id, similarity) AS (
    SELECT left_id, right_id, similarity FROM eligible_edges
    UNION ALL
    SELECT right_id, left_id, similarity FROM eligible_edges
),
reachability(root_id, node_id) AS (
    SELECT notification_id, notification_id FROM recent
    UNION
    SELECT r.root_id, l.linked_id
    FROM reachability AS r
    JOIN links AS l ON l.node_id = r.node_id
),
components AS (
    SELECT node_id, MIN(root_id) AS thread_key
    FROM reachability
    GROUP BY node_id
),
members AS (
    SELECT c.thread_key, r.notification_id, r.source_app, r.sender, r.title,
           r.content, r.received_at, r.category_id
    FROM components AS c
    JOIN recent AS r ON r.notification_id = c.node_id
),
cross_app_threads AS (
    SELECT thread_key
    FROM members
    GROUP BY thread_key
    HAVING COUNT(*) > 1
       AND COUNT(DISTINCT source_app) > 1
)
SELECT m.thread_key AS duplicate_thread_id,
       MIN(m.received_at) AS first_received_at,
       MAX(m.received_at) AS last_received_at,
       COUNT(*) AS notification_count,
       COUNT(DISTINCT m.source_app) AS source_application_count,
       GROUP_CONCAT(DISTINCT m.source_app) AS source_applications,
       GROUP_CONCAT(m.notification_id) AS notification_ids,
       GROUP_CONCAT(COALESCE(m.title, m.sender) || ': ' || m.content, ' || ')
           AS thread_messages
FROM members AS m
JOIN cross_app_threads AS t ON t.thread_key = m.thread_key
GROUP BY m.thread_key
ORDER BY last_received_at DESC;
