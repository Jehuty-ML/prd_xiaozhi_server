package xiaozhi.modules.agent.mq;

import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import xiaozhi.modules.agent.dto.AgentChatHistoryReportDTO;
import xiaozhi.modules.agent.service.biz.AgentChatHistoryBizService;

/**
 * Consumes agent chat-history events and persists via existing Biz service.
 */
@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "xiaozhi.rabbitmq", name = "enabled", havingValue = "true")
public class ChatHistoryMqListener {

    private final AgentChatHistoryBizService agentChatHistoryBizService;
    private final ObjectMapper objectMapper;

    @RabbitListener(queues = "${xiaozhi.rabbitmq.queue:xiaozhi.chat.history}")
    public void onMessage(String body) {
        try {
            JsonNode node = objectMapper.readTree(body);
            AgentChatHistoryReportDTO report = new AgentChatHistoryReportDTO();
            report.setMacAddress(text(node, "macAddress"));
            report.setSessionId(text(node, "sessionId"));
            report.setContent(text(node, "content"));
            report.setAudioBase64(textOrNull(node, "audioBase64"));
            if (node.hasNonNull("chatType")) {
                report.setChatType(node.get("chatType").numberValue().byteValue());
            }
            if (node.hasNonNull("reportTime")) {
                long raw = node.get("reportTime").asLong();
                // Accept 10-digit seconds or 13-digit millis; Biz uses Date(millis).
                report.setReportTime(raw < 1_000_000_000_000L ? raw * 1000L : raw);
            }
            if (report.getMacAddress() == null || report.getMacAddress().isBlank()
                    || report.getSessionId() == null || report.getSessionId().isBlank()
                    || report.getContent() == null || report.getContent().isBlank()
                    || report.getChatType() == null) {
                log.warn("丢弃非法聊天历史消息: {}", body);
                return;
            }
            Boolean ok = agentChatHistoryBizService.report(report);
            log.debug("MQ 聊天历史上报结果 mac={} type={} ok={}", report.getMacAddress(),
                    report.getChatType(), ok);
        } catch (Exception e) {
            log.error("处理聊天历史 MQ 消息失败，丢弃: {}", body, e);
        }
    }

    private static String text(JsonNode node, String field) {
        JsonNode v = node.get(field);
        return v == null || v.isNull() ? null : v.asText();
    }

    private static String textOrNull(JsonNode node, String field) {
        JsonNode v = node.get(field);
        if (v == null || v.isNull()) {
            return null;
        }
        String s = v.asText();
        return s == null || s.isBlank() ? null : s;
    }
}
