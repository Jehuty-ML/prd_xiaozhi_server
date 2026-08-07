package xiaozhi.modules.sys.enums;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

/**
 * 服务端动作枚举
 */
public enum ServerActionEnum {
    RESTART("restart"),
    UPDATE_CONFIG("update_config"),
    /** 向该 WS 实例全部在线设备广播播报文案 */
    BROADCAST_SPEAK("broadcast_speak");

    private final String value;

    ServerActionEnum(String value) {
        this.value = value;
    }

    @JsonValue
    public String getValue() {
        return value;
    }

    @JsonCreator
    public static ServerActionEnum fromValue(String value) {
        for (ServerActionEnum action : ServerActionEnum.values()) {
            if (action.value.equalsIgnoreCase(value)) {
                return action;
            }
        }
        return null;
    }
}
