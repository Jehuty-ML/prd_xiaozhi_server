package xiaozhi.modules.device.dto;

import java.io.Serializable;

import lombok.Data;

/**
 * Dialogue 实例注册信息（与 Python dialogue_registry / Java DialogueServerInfo 对齐）
 */
@Data
public class DialogueServerInfo implements Serializable {
    private static final long serialVersionUID = 1L;

    private String instanceId;
    private String websocketAddress;
    private String udpAddress;
    private String otaAddress;
    private String mcpAddress;
    private String serverAddress;
    private long lastHeartbeat;
}
