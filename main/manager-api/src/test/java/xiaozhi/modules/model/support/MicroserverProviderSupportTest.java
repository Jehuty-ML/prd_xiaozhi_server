package xiaozhi.modules.model.support;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

import cn.hutool.json.JSONObject;
import xiaozhi.modules.model.entity.ModelConfigEntity;

class MicroserverProviderSupportTest {

    @Test
    void openaiLlmSupported() {
        ModelConfigEntity entity = new ModelConfigEntity();
        entity.setModelType("LLM");
        JSONObject json = new JSONObject();
        json.set("type", "openai");
        entity.setConfigJson(json);
        assertTrue(MicroserverProviderSupport.isSupported(entity));
    }

    @Test
    void difyLlmUnsupported() {
        ModelConfigEntity entity = new ModelConfigEntity();
        entity.setModelType("LLM");
        JSONObject json = new JSONObject();
        json.set("type", "dify");
        entity.setConfigJson(json);
        assertFalse(MicroserverProviderSupport.isSupported(entity));
        assertTrue(MicroserverProviderSupport.unsupportedMessage(entity).contains("还不支持"));
    }
}
