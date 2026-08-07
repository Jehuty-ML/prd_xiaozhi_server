-- 唤醒 DETECT 空窗超时（无人说话回 IDLE）

DELETE FROM `sys_params` WHERE `param_code` = 'detect_timeout_seconds';

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(313, 'detect_timeout_seconds', '10', 'number', 1, '唤醒 DETECT 后无人说话回 IDLE 的空窗超时(秒)；<=0 关闭');
