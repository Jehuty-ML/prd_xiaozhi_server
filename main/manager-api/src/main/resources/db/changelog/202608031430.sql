-- 混沌注入 + 专用 TTS 预置音路径

DELETE FROM `sys_params` WHERE `param_code` IN (
  'server.resilience.tts_fallback_audio',
  'server.resilience.chaos.enabled',
  'server.resilience.chaos.asr_fail_rate',
  'server.resilience.chaos.llm_fail_rate',
  'server.resilience.chaos.tts_fail_rate'
);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(156, 'server.resilience.tts_fallback_audio', 'config/assets/tts_fallback.wav', 'string', 1, 'TTS/降级预置音路径（专用资产，默认由 wakeup 短音复制）'),
(157, 'server.resilience.chaos.enabled', 'false', 'boolean', 1, '混沌注入总开关（生产必须 false）'),
(158, 'server.resilience.chaos.asr_fail_rate', '0', 'number', 1, 'ASR 混沌失败概率 0~1'),
(159, 'server.resilience.chaos.llm_fail_rate', '0', 'number', 1, 'LLM 混沌失败概率 0~1'),
(160, 'server.resilience.chaos.tts_fail_rate', '0', 'number', 1, 'TTS 混沌失败概率 0~1');
