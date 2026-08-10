package xiaozhi.modules.model.support;

import java.util.Locale;
import java.util.Map;
import java.util.Set;

import cn.hutool.json.JSONObject;
import xiaozhi.modules.model.entity.ModelConfigEntity;

/**
 * xiaozhi-microserver 当前已实现的模型接口类型（config_json.type）。
 * 智控台启用/设默认时拦截未支持类型，避免静默下发到微服务后失败。
 */
public final class MicroserverProviderSupport {

    private static final Map<String, Set<String>> SUPPORTED = Map.of(
            "LLM", Set.of("echo", "openai", "openai_compat", "openai-compat"),
            "ASR", Set.of("stub", "openai", "openai_compat", "whisper", "fun_local", "funasr", "doubao"),
            "TTS", Set.of("echo", "edge", "edgetts", "doubao"),
            "VAD", Set.of("stub", "silero", "silero_vad"),
            "MEMORY", Set.of("nomem"),
            "INTENT", Set.of("function_call", "nointent"),
            "VLLM", Set.of("openai", "openai_compat", "openai-compat"));

    private MicroserverProviderSupport() {
    }

    public static String typeOf(ModelConfigEntity entity) {
        if (entity == null || entity.getConfigJson() == null) {
            return "";
        }
        JSONObject json = entity.getConfigJson();
        Object type = json.get("type");
        return type == null ? "" : String.valueOf(type).trim();
    }

    public static boolean isSupported(ModelConfigEntity entity) {
        if (entity == null) {
            return false;
        }
        String modelType = entity.getModelType() == null ? "" : entity.getModelType().trim().toUpperCase(Locale.ROOT);
        Set<String> allowed = SUPPORTED.get(modelType);
        if (allowed == null) {
            // Plugin 等未列入的类别不拦截
            return true;
        }
        String type = typeOf(entity).toLowerCase(Locale.ROOT);
        return allowed.contains(type);
    }

    public static String unsupportedMessage(ModelConfigEntity entity) {
        String type = typeOf(entity);
        String modelType = entity == null ? "" : entity.getModelType();
        return "当前 xiaozhi-microserver 还不支持该模型接口类型: " + modelType + "/" + type
                + "（请选用 openai / doubao / edge / silero / fun_local / nomem 等已实现类型）";
    }
}
