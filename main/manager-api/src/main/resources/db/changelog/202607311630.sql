-- 连接硬上限参数（供 xiaozhi-server 从智控台下发）
-- 点号嵌套后对应 server.connection.*

DELETE FROM `sys_params` WHERE `param_code` IN (
  'server.connection.max_connections',
  'server.connection.max_connections_per_device',
  'server.connection.report_queue_maxsize',
  'server.connection.cleanup_timeout_seconds'
);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(123, 'server.connection.max_connections', '500', 'number', 1, '单进程最大并发 WebSocket 连接数'),
(124, 'server.connection.max_connections_per_device', '2', 'number', 1, '同一 device-id 最大并发连接数'),
(125, 'server.connection.report_queue_maxsize', '100', 'number', 1, '聊天上报队列上限，满则丢弃'),
(126, 'server.connection.cleanup_timeout_seconds', '10', 'number', 1, '连接清理等待超时（秒）');
