-- Redis 共享熔断

DELETE FROM `sys_params` WHERE `param_code` IN (
  'server.resilience.redis.enabled',
  'server.resilience.redis.host',
  'server.resilience.redis.port',
  'server.resilience.redis.password',
  'server.resilience.redis.db',
  'server.resilience.redis.key_prefix',
  'server.resilience.redis.fallback_local',
  'server.tracing.enabled',
  'server.tracing.service_name',
  'server.tracing.exporter',
  'server.tracing.endpoint',
  'server.tracing.sample_ratio'
);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(161, 'server.resilience.redis.enabled', 'false', 'boolean', 1, '是否用 Redis 共享熔断状态（多实例）；连不上时按 fallback_local 回退'),
(162, 'server.resilience.redis.host', '127.0.0.1', 'string', 1, '熔断 Redis 主机'),
(163, 'server.resilience.redis.port', '6379', 'number', 1, '熔断 Redis 端口'),
(164, 'server.resilience.redis.password', '', 'string', 1, '熔断 Redis 密码（可空）'),
(165, 'server.resilience.redis.db', '1', 'number', 1, '熔断 Redis DB（建议与 manager-api 的 0 隔离）'),
(166, 'server.resilience.redis.key_prefix', 'xiaozhi:circuit:', 'string', 1, '熔断 Redis key 前缀'),
(167, 'server.resilience.redis.fallback_local', 'true', 'boolean', 1, 'Redis 不可用时回退进程内熔断');
