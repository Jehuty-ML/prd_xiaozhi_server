package xiaozhi.modules.agent.mq;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import com.fasterxml.jackson.databind.ObjectMapper;

import xiaozhi.modules.agent.dto.AgentChatHistoryReportDTO;
import xiaozhi.modules.agent.service.biz.AgentChatHistoryBizService;

class ChatHistoryMqListenerTest {

    private AgentChatHistoryBizService biz;
    private ChatHistoryMqListener listener;

    @BeforeEach
    void setUp() {
        biz = mock(AgentChatHistoryBizService.class);
        when(biz.report(any())).thenReturn(Boolean.TRUE);
        listener = new ChatHistoryMqListener(biz, new ObjectMapper());
    }

    @Test
    void onMessageMapsSecondsReportTimeToMillis() {
        String body = """
                {"macAddress":"aa:bb","sessionId":"s1","chatType":1,"content":"hi","reportTime":1745657732}
                """;

        listener.onMessage(body);

        ArgumentCaptor<AgentChatHistoryReportDTO> cap = ArgumentCaptor.forClass(AgentChatHistoryReportDTO.class);
        verify(biz).report(cap.capture());
        AgentChatHistoryReportDTO dto = cap.getValue();
        assertEquals("aa:bb", dto.getMacAddress());
        assertEquals("s1", dto.getSessionId());
        assertEquals(Byte.valueOf((byte) 1), dto.getChatType());
        assertEquals("hi", dto.getContent());
        assertEquals(1745657732000L, dto.getReportTime());
        assertNull(dto.getAudioBase64());
    }

    @Test
    void onMessageDropsInvalidPayload() {
        listener.onMessage("{\"macAddress\":\"\",\"sessionId\":\"s\",\"chatType\":1,\"content\":\"x\"}");
        verify(biz, never()).report(any());
    }
}
