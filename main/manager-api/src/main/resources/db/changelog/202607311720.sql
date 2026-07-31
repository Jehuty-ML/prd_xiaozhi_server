-- 运行环境：development | production
DELETE FROM `sys_params` WHERE `param_code` = 'server.environment';

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(129, 'server.environment', 'development', 'string', 1, '运行环境：development（联调兼容）或 production（禁止 query token / 硬编码 key 兜底）。也可用环境变量 XIAOZHI_ENV 覆盖');
