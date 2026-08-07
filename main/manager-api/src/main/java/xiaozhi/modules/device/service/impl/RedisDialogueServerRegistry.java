package xiaozhi.modules.device.service.impl;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ThreadLocalRandom;

import org.apache.commons.lang3.StringUtils;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import com.fasterxml.jackson.databind.ObjectMapper;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import xiaozhi.common.redis.RedisKeys;
import xiaozhi.modules.device.dto.DialogueServerInfo;
import xiaozhi.modules.device.service.DialogueServerRegistry;

/**
 * 基于 Redis 的 Dialogue 注册发现（与 Python xiaozhi-server 心跳协议一致）
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class RedisDialogueServerRegistry implements DialogueServerRegistry {

    private final StringRedisTemplate stringRedisTemplate;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Override
    public List<DialogueServerInfo> getAvailableServers() {
        List<DialogueServerInfo> result = new ArrayList<>();
        try {
            Map<Object, Object> entries = stringRedisTemplate.opsForHash()
                    .entries(RedisKeys.DIALOGUE_SERVERS);
            if (entries == null || entries.isEmpty()) {
                return result;
            }

            List<Map.Entry<Object, Object>> serverEntries = new ArrayList<>(entries.entrySet());
            List<String> heartbeatKeys = new ArrayList<>(serverEntries.size());
            for (Map.Entry<Object, Object> entry : serverEntries) {
                heartbeatKeys.add(RedisKeys.getDialogueHeartbeatKey(String.valueOf(entry.getKey())));
            }

            List<String> heartbeatValues = stringRedisTemplate.opsForValue().multiGet(heartbeatKeys);
            for (int i = 0; i < serverEntries.size(); i++) {
                Map.Entry<Object, Object> entry = serverEntries.get(i);
                String instanceId = String.valueOf(entry.getKey());
                String heartbeatValue = heartbeatValues != null && i < heartbeatValues.size()
                        ? heartbeatValues.get(i)
                        : null;
                if (heartbeatValue != null) {
                    DialogueServerInfo info = parseInfo(entry.getValue());
                    if (info != null && StringUtils.isNotBlank(info.getWebsocketAddress())) {
                        result.add(info);
                    }
                    continue;
                }
                stringRedisTemplate.opsForHash().delete(RedisKeys.DIALOGUE_SERVERS, instanceId);
                log.info("清理过期的 Dialogue 服务器: {}", instanceId);
            }
        } catch (Exception e) {
            log.error("获取可用 Dialogue 服务器列表失败", e);
        }
        return result;
    }

    @Override
    public DialogueServerInfo selectServer() {
        List<DialogueServerInfo> servers = getAvailableServers();
        if (servers.isEmpty()) {
            return null;
        }
        int index = ThreadLocalRandom.current().nextInt(servers.size());
        return servers.get(index);
    }

    @Override
    public int countAvailableServers() {
        return getAvailableServers().size();
    }

    private DialogueServerInfo parseInfo(Object raw) {
        if (raw == null) {
            return null;
        }
        try {
            String json = raw instanceof String ? (String) raw : String.valueOf(raw);
            if (StringUtils.isBlank(json)) {
                return null;
            }
            return objectMapper.readValue(json, DialogueServerInfo.class);
        } catch (Exception e) {
            log.warn("解析 DialogueServerInfo 失败: {}", e.getMessage());
            return null;
        }
    }
}
