package xiaozhi.modules.agent.mq;

import org.springframework.amqp.core.Queue;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Declares chat-history queue when RabbitMQ consumer is enabled.
 */
@Configuration
@ConditionalOnProperty(prefix = "xiaozhi.rabbitmq", name = "enabled", havingValue = "true")
public class ChatHistoryRabbitConfig {

    @Bean
    public Queue chatHistoryQueue(RabbitMqProperties properties) {
        return new Queue(properties.getQueue(), true);
    }
}
