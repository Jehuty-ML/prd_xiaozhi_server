-- 预算 / 过载背压 / 降级话术扩展

DELETE FROM `sys_params` WHERE `param_code` IN (
  'server.resilience.llm_timeout_seconds',
  'server.resilience.llm_ttfb_deadline_seconds',
  'server.resilience.round_deadline_seconds',
  'server.resilience.overload.enabled',
  'server.resilience.overload.connection_usage_threshold',
  'server.resilience.overload.tts_text_queue_threshold',
  'server.resilience.overload.tts_audio_queue_threshold',
  'server.resilience.overload.report_queue_usage_threshold',
  'server.resilience.overload'
);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(147, 'server.resilience.llm_timeout_seconds', '90', 'number', 1, 'LLM 阶段超时参考/TTFB 兜底（秒）'),
(148, 'server.resilience.llm_ttfb_deadline_seconds', '25', 'number', 1, '单轮 LLM 首 token 预算（秒）；不是整轮 ASR+LLM+TTS'),
(149, 'server.resilience.round_deadline_seconds', '120', 'number', 1, '单轮整轮预算（秒，含流式输出）；超时结束本轮并降级'),
(150, 'server.resilience.overload.enabled', 'true', 'boolean', 1, '是否启用过载背压（连接/队列水位过高时降级新对话）'),
(151, 'server.resilience.overload.connection_usage_threshold', '0.85', 'number', 1, '连接使用率阈值（active/max），达到则过载降级'),
(152, 'server.resilience.overload.tts_text_queue_threshold', '80', 'number', 1, 'TTS 文本队列深度阈值'),
(153, 'server.resilience.overload.tts_audio_queue_threshold', '120', 'number', 1, 'TTS 音频队列深度阈值'),
(154, 'server.resilience.overload.report_queue_usage_threshold', '0.9', 'number', 1, '上报队列使用率阈值'),
(155, 'server.resilience.overload', '现在有点忙不过来，请稍后再试一下。', 'string', 1, '过载降级话术');
