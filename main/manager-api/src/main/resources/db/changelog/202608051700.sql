-- 修复：过载话术勿用 server.resilience.overload 标量（与 overload.* 嵌套 Map 冲突）
-- 正确键：server.resilience.overload.message → resilience.overload.message

DELETE FROM `sys_params` WHERE `param_code` IN (
  'server.resilience.overload',
  'server.resilience.overload_message',
  'server.resilience.overload.message'
);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(173, 'server.resilience.overload.message', '现在有点忙不过来，请稍后再试一下。', 'string', 1, '过载降级话术（挂在 overload 对象下，勿用同名标量键）');
