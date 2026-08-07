package xiaozhi.modules.device.service;

import java.util.List;

import xiaozhi.modules.device.dto.DialogueServerInfo;

/**
 * Dialogue 实例注册发现（读 Redis 心跳，供 OTA 选路）
 */
public interface DialogueServerRegistry {

    /**
     * 返回心跳仍有效的 Dialogue 实例
     */
    List<DialogueServerInfo> getAvailableServers();

    /**
     * 随机选一个存活实例；无可用时返回 null
     */
    DialogueServerInfo selectServer();

    /**
     * 存活实例数（供 OTA 探活展示）
     */
    int countAvailableServers();
}
