package xiaozhi.modules.sys.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * 管理台向 WS 实例全部在线设备广播播报文案
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
@Schema(description = "广播播报文案")
public class BroadcastSpeakDTO {
    @Schema(description = "目标 dialogue WS 地址")
    @NotBlank(message = "targetWs不能为空")
    private String targetWs;

    @Schema(description = "要播报的文本")
    @NotBlank(message = "播报文案不能为空")
    @Size(max = 500, message = "播报文案最多500字")
    private String text;
}
