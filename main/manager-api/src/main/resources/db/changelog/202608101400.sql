-- microserver 分支：未实现的接口类型默认禁用（按 config_json.type）
-- 已支持：openai / echo / stub / doubao / fun_local|funasr / edge / silero / nomem / function_call|nointent

UPDATE `ai_model_config`
SET `is_enabled` = 0
WHERE `is_enabled` = 1
  AND LOWER(IFNULL(JSON_UNQUOTE(JSON_EXTRACT(`config_json`, '$.type')), '')) NOT IN (
    'openai', 'openai_compat', 'openai-compat',
    'echo', 'stub',
    'doubao',
    'fun_local', 'funasr',
    'edge', 'edgetts',
    'silero', 'silero_vad',
    'nomem',
    'function_call', 'nointent',
    'whisper'
  );

-- 被禁用的不再作为默认
UPDATE `ai_model_config`
SET `is_default` = 0
WHERE `is_default` = 1
  AND `is_enabled` = 0;

-- 若 LLM 没有启用中的默认项，回落豆包（openai，microserver 已支持）
SET @llm_default_ok := (
  SELECT COUNT(*) FROM `ai_model_config`
  WHERE `model_type` = 'LLM' AND `is_default` = 1 AND `is_enabled` = 1
);
UPDATE `ai_model_config`
SET `is_enabled` = 1, `is_default` = 1
WHERE `id` = 'LLM_DoubaoLLM'
  AND @llm_default_ok = 0;
