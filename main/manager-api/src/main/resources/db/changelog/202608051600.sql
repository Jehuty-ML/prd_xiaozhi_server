-- 有界队列容量：TTS/ASR 满则丢最旧，避免无限堆积

DELETE FROM `sys_params` WHERE `param_code` IN (
  'server.resilience.overload.tts_text_queue_maxsize',
  'server.resilience.overload.tts_audio_queue_maxsize',
  'server.resilience.overload.asr_audio_queue_maxsize'
);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(170, 'server.resilience.overload.tts_text_queue_maxsize', '80', 'number', 1, 'TTS 文本队列有界容量（满则丢最旧）；建议 ≥ tts_text_queue_threshold'),
(171, 'server.resilience.overload.tts_audio_queue_maxsize', '120', 'number', 1, 'TTS 音频队列有界容量（满则丢最旧）；建议 ≥ tts_audio_queue_threshold'),
(172, 'server.resilience.overload.asr_audio_queue_maxsize', '200', 'number', 1, 'ASR PCM 缓冲有界容量（满则丢最旧帧）');
