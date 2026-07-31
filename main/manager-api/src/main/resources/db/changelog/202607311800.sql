-- 上游依赖韧性：超时/重试/熔断/降级话术（供 xiaozhi-server 从智控台下发）
-- 点号嵌套后对应 server.resilience.*

DELETE FROM `sys_params` WHERE `param_code` IN (
  'server.resilience.enabled',
  'server.resilience.max_retries',
  'server.resilience.retry_delay_seconds',
  'server.resilience.asr_timeout_seconds',
  'server.resilience.llm_timeout_seconds',
  'server.resilience.tts_max_retries',
  'server.resilience.tts_retry_delay_seconds',
  'server.resilience.circuit_failure_threshold',
  'server.resilience.circuit_open_seconds',
  'server.resilience.asr',
  'server.resilience.asr_empty',
  'server.resilience.llm',
  'server.resilience.tts',
  'server.resilience.tool'
);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(130, 'server.resilience.enabled', 'true', 'boolean', 1, '是否启用上游依赖韧性（超时/重试/熔断/降级）'),
(131, 'server.resilience.max_retries', '2', 'number', 1, '上游瞬时故障最大重试次数'),
(132, 'server.resilience.retry_delay_seconds', '0.5', 'number', 1, '重试间隔（秒）'),
(133, 'server.resilience.asr_timeout_seconds', '15', 'number', 1, 'ASR 单次超时（秒）'),
(134, 'server.resilience.llm_timeout_seconds', '60', 'number', 1, 'LLM 相关超时参考值（秒）'),
(135, 'server.resilience.tts_max_retries', '3', 'number', 1, 'TTS 合成最大重试次数'),
(136, 'server.resilience.tts_retry_delay_seconds', '0.3', 'number', 1, 'TTS 重试间隔（秒）'),
(137, 'server.resilience.circuit_failure_threshold', '5', 'number', 1, '熔断：连续失败达此次数后开路'),
(138, 'server.resilience.circuit_open_seconds', '30', 'number', 1, '熔断开路冷却时间（秒）'),
(139, 'server.resilience.asr', '不好意思，我没听清楚，请再说一遍。', 'string', 1, 'ASR 失败降级话术'),
(140, 'server.resilience.asr_empty', '不好意思，我没听清楚，请再说一遍。', 'string', 1, 'ASR 空识别降级话术'),
(141, 'server.resilience.llm', '主人，小智现在有点忙，我们稍后再试吧。', 'string', 1, 'LLM 失败降级话术'),
(142, 'server.resilience.tts', '抱歉，我暂时说不出话，请稍后再试。', 'string', 1, 'TTS 失败降级话术（日志/后续预置音）'),
(143, 'server.resilience.tool', '哎呀，网络遇到点问题，请稍后再试下！', 'string', 1, '工具调用超时/失败降级话术');
