-- 认证白名单策略：免检开关 / 仅白名单可接入
-- allow_whitelist_bypass=auto：未强制时由 xiaozhi-server 按 environment 决定
--   development 允许免检；production 禁止免检

DELETE FROM `sys_params` WHERE `param_code` IN (
  'server.auth.allow_whitelist_bypass',
  'server.auth.devices_allowlist_only'
);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(316, 'server.auth.allow_whitelist_bypass', 'auto', 'string', 1, '白名单免 token：auto=随环境（dev允许/prod禁止）；true/false=强制'),
(317, 'server.auth.devices_allowlist_only', 'false', 'boolean', 1, '仅白名单设备可接入：true 且 allowed_devices 非空时，未列入即使有 token 也拒绝');
