-- 过载背压：用全局在途对话/LLM 替换连接使用率阈值

DELETE FROM `sys_params` WHERE `param_code` IN (
  'server.resilience.overload.connection_usage_threshold',
  'server.resilience.overload.max_concurrent_chats',
  'server.resilience.overload.max_concurrent_llm'
);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark) VALUES
(168, 'server.resilience.overload.max_concurrent_chats', '80', 'number', 1, '全局同时进行中的对话轮次上限（listen→LLM→TTS）；0=不限制'),
(169, 'server.resilience.overload.max_concurrent_llm', '80', 'number', 1, '全局同时占用上游 LLM 的流式调用上限；0=不限制');

UPDATE `sys_params` SET `remark`='是否启用过载背压（在途对话/LLM 与队列水位过高时降级新对话）'
WHERE `param_code`='server.resilience.overload.enabled';
