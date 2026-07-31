-- Prometheus 指标开关与路径（供 xiaozhi-server 从智控台下发）
-- 点号嵌套后对应 server.metrics.*

DELETE FROM `sys_params` WHERE `param_code` IN (
  'server.metrics.enabled',
  'server.metrics.path'
);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(127, 'server.metrics.enabled', 'true', 'boolean', 1, '是否启用 Prometheus 指标（HTTP /metrics）'),
(128, 'server.metrics.path', '/metrics', 'string', 1, 'Prometheus 指标路径，默认 /metrics，挂在 http_port');
