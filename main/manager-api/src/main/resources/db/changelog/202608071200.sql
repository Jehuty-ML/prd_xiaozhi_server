-- 会话状态机 mode：common（默认）/ play_only（仅播放）
-- 全局参数：智控台改参后「通知更新配置」向本 WS 实例全部在线设备广播，强制打断回 IDLE

DELETE FROM `sys_params` WHERE `param_code` IN (
  'session_state.mode',
  'session_state.play_only_deny_text'
);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(314, 'session_state.mode', 'common', 'string', 1, '会话状态机全局模式：common=正常对话；play_only=仅播放。改后需在服务端管理点「通知更新配置」广播全员'),
(315, 'session_state.play_only_deny_text', '当前不能对话', 'string', 1, 'play_only 模式下听到唤醒词时的降级播报文案');
