package xiaozhi.modules.agent.mq;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

import lombok.Data;

/**
 * RabbitMQ chat-history consumer settings (disabled by default).
 */
@Data
@Component
@ConfigurationProperties(prefix = "xiaozhi.rabbitmq")
public class RabbitMqProperties {
    /** When false, Spring AMQP listener auto-startup stays off. */
    private boolean enabled = false;
    private String host = "127.0.0.1";
    private int port = 5672;
    private String username = "guest";
    private String password = "guest";
    private String virtualHost = "/";
    private String queue = "xiaozhi.chat.history";
}
