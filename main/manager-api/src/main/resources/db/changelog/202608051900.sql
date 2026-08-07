-- Dialogue 实例 Redis 注册心跳（多实例发现，供 OTA 选存活 WS）

DELETE FROM `sys_params` WHERE `param_code` IN (
  'server.registry.enabled',
  'server.registry.instance_id',
  'server.registry.heartbeat_interval_seconds',
  'server.registry.heartbeat_ttl_seconds',
  'server.registry.redis.url',
  'server.registry.redis.host',
  'server.registry.redis.port',
  'server.registry.redis.password',
  'server.registry.redis.db',
  'server.registry.redis.socket_timeout'
);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(175, 'server.registry.enabled', 'true', 'boolean', 1, '是否启用 Dialogue 多实例 Redis 注册心跳；OTA 优先选存活实例'),
(176, 'server.registry.instance_id', '', 'string', 1, '可选固定实例 ID；空则用主机名+短 UUID（也可用环境变量 XIAOZHI_INSTANCE_ID）'),
(177, 'server.registry.heartbeat_interval_seconds', '30', 'number', 1, '注册心跳间隔（秒）'),
(178, 'server.registry.heartbeat_ttl_seconds', '60', 'number', 1, '心跳 TTL（秒）；建议 ≥ 2×interval，过期视为假存活'),
(179, 'server.registry.redis.url', '', 'string', 1, '注册 Redis URL（若设置则优先于 host/port；可空）'),
(180, 'server.registry.redis.host', '127.0.0.1', 'string', 1, '注册 Redis 主机（须与 manager-api 同实例）'),
(181, 'server.registry.redis.port', '6379', 'number', 1, '注册 Redis 端口'),
(182, 'server.registry.redis.password', '', 'string', 1, '注册 Redis 密码（可空）'),
(183, 'server.registry.redis.db', '0', 'number', 1, '注册 Redis DB（必须与 manager-api 同库，默认 0；勿与熔断 db=1 混用）'),
(184, 'server.registry.redis.socket_timeout', '1.0', 'number', 1, '注册 Redis 读写超时（秒）');
