-- 过载检测补上 AudioRateController 待发音频积压阈值

DELETE FROM `sys_params` WHERE `param_code` IN (
  'server.resilience.overload.audio_rate_queue_threshold'
);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(174, 'server.resilience.overload.audio_rate_queue_threshold', '80', 'number', 1, 'AudioRateController 待发音频帧数阈值（≈60ms/帧）；播发线程会把包从 tts_audio_queue 挪到此处');
