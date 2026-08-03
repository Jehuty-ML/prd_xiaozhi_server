-- TTS 预置音 + LLM fallback（server.resilience 扩展）

DELETE FROM `sys_params` WHERE `param_code` IN (
  'server.resilience.llm_fallback',
  'server.resilience.tts_fallback_audio',
  'server.resilience.use_tts_fallback_on_degrade'
);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(144, 'server.resilience.llm_fallback', '', 'string', 1, 'LLM 备用配置名（对应 config LLM 段键名，空=关闭；勿与主 LLM 相同）'),
(145, 'server.resilience.tts_fallback_audio', 'config/assets/wakeup_words_short.wav', 'string', 1, 'TTS 失败/降级时本地预置音路径（不依赖 TTS API）'),
(146, 'server.resilience.use_tts_fallback_on_degrade', 'true', 'boolean', 1, 'ASR/LLM 降级是否优先播放预置音（true 时不走 text_to_speak）');
