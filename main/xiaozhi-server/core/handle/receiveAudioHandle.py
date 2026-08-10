import time
import json
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core.utils.util import audio_to_data
from core.handle.abortHandle import handleAbortMessage
from core.handle.intentHandler import handle_user_intent
from core.utils.output_counter import check_device_output_limit
from core.handle.sendAudioHandle import send_stt_message, SentenceType
from core.utils.session_state import (
    SessionEvent,
    SessionState,
    is_play_only_mode,
    speak_play_only_denied,
)

TAG = __name__


async def handleAudioMessage(conn: "ConnectionHandler", pcm_frame):
    # play_only：不收音、不跑 ASR
    if is_play_only_mode(conn):
        return
    # 当前片段是否有人说话
    have_voice = conn.vad.is_vad(conn, pcm_frame)
    # 如果设备刚刚被唤醒，短暂忽略VAD检测
    if hasattr(conn, "just_woken_up") and conn.just_woken_up:
        have_voice = False
        # 设置一个短暂延迟后恢复VAD检测
        if not hasattr(conn, "vad_resume_task") or conn.vad_resume_task.done():
            if hasattr(conn, "spawn_task"):
                conn.vad_resume_task = conn.spawn_task(resume_vad_detection(conn))
            else:
                conn.vad_resume_task = asyncio.create_task(resume_vad_detection(conn))
        return
    # 服务端AEC功能需要实时触发打断
    if conn.client_aec and have_voice:
        if conn.client_is_speaking and conn.client_listen_mode != "manual":
            await handleAbortMessage(conn)
    # 设备长时间空闲检测，用于say goodbye
    await no_voice_close_connect(conn, have_voice)
    # 接收音频
    await conn.asr.receive_audio(conn, pcm_frame, have_voice)


async def resume_vad_detection(conn: "ConnectionHandler"):
    # 等待2秒后恢复VAD检测
    await asyncio.sleep(2)
    conn.just_woken_up = False


async def startToChat(conn: "ConnectionHandler", text):
    # play_only：禁止对话；若由唤醒路径进来则播降级话
    if is_play_only_mode(conn):
        conn.logger.bind(tag=TAG).info("play_only 拒绝 startToChat，播报降级话术")
        speak_play_only_denied(conn)
        return False

    # 检查输入是否是JSON格式（包含说话人信息）
    speaker_name = None
    actual_text = text

    try:
        # 尝试解析JSON格式的输入
        if text.strip().startswith("{") and text.strip().endswith("}"):
            data = json.loads(text)
            if "speaker" in data and "content" in data:
                speaker_name = data["speaker"]
                actual_content = data["content"]
                conn.logger.bind(tag=TAG).info(f"解析到说话人信息: {speaker_name}")

                # 仅在该说话人首次出现时保留 {"speaker":...} JSON，让模型自然称呼一次；
                # 后续轮降为纯文本，避免每轮重复出现名字诱导模型反复称呼
                if speaker_name not in conn.introduced_speakers:
                    conn.introduced_speakers.add(speaker_name)
                    actual_text = text
                else:
                    actual_text = actual_content
    except (json.JSONDecodeError, KeyError):
        # 如果解析失败，继续使用原始文本
        pass

    # 保存说话人信息到连接对象
    if speaker_name:
        conn.current_speaker = speaker_name
    else:
        conn.current_speaker = None

    if conn.need_bind:
        await check_bind_device(conn)
        return False

    # 过载背压：连接/队列水位过高时直接降级，避免越拖越慢
    from core.utils.resilience import (
        UpstreamKind,
        check_system_overload,
        speak_degradation,
    )
    from core.utils import metrics as metrics_mod

    overload_reason = check_system_overload(conn)
    if overload_reason:
        conn.logger.bind(tag=TAG).warning(f"过载降级，跳过本轮 chat: {overload_reason}")
        metrics_mod.observe_overload_shed(overload_reason)
        speak_degradation(
            conn,
            "overload",
            UpstreamKind.OVERLOAD,
            overload_reason=overload_reason,
        )
        return False

    # 如果当日的输出字数大于限定的字数
    if conn.max_output_size > 0:
        if check_device_output_limit(
            conn.headers.get("device-id"), conn.max_output_size
        ):
            await max_out_size(conn)
            return True

    # manual 模式下不打断正在播放的内容
    if conn.client_is_speaking and conn.client_listen_mode != "manual":
        await handleAbortMessage(conn)

    # 首先进行意图分析，使用实际文本内容
    intent_handled = await handle_user_intent(conn, actual_text)

    # await 之后可能已被切到 play_only（配置热更新/广播），需再校验。
    # 必须在 intent_handled 早退之前：否则意图路径会绕过 play_only 门禁。
    if is_play_only_mode(conn):
        # 意图路径若已播降级话并返回 True，避免重复播报
        if not intent_handled:
            conn.logger.bind(tag=TAG).info(
                "play_only 拒绝 startToChat（await 后复核），播报降级话术"
            )
            speak_play_only_denied(conn)
        return False

    if intent_handled:
        # 如果意图已被处理，不再进行聊天
        return True

    # 意图未被处理，继续常规聊天流程，使用实际文本内容
    if not conn.transition_session(SessionEvent.CHAT_START, detail="start_to_chat"):
        conn.logger.bind(tag=TAG).warning(
            "CHAT_START 被拒绝，跳过本轮 chat"
        )
        return False
    try:
        await send_stt_message(conn, actual_text)
    except Exception:
        # STT/TTS start 发送失败时回滚，避免永久卡在 THINKING（无法 DETECT/LISTEN）
        if conn.session_sm.is_in(SessionState.THINKING):
            conn.transition_session(SessionEvent.RESET, detail="stt_send_failed")
        raise

    # send_stt await 期间可能已切入广播/play_only；禁止清 abort 并启动 chat（会抢写 sentence_id）
    if is_play_only_mode(conn) or getattr(conn, "_broadcast_speak_active", False) or getattr(
        conn, "_broadcast_soft_barge_in", False
    ):
        conn.logger.bind(tag=TAG).info(
            "play_only/广播中拒绝 startToChat（STT await 后复核），不启动 chat"
        )
        if conn.session_sm.is_in(SessionState.THINKING):
            conn.transition_session(SessionEvent.RESET, detail="stt_after_play_only")
        speak_play_only_denied(conn)
        return False

    # 准备开始新会话
    conn.client_abort = False

    conn.executor.submit(conn.chat, actual_text)
    return True


async def no_voice_close_connect(conn: "ConnectionHandler", have_voice):
    if have_voice:
        conn.last_activity_time = time.time() * 1000
        return
    # 空闲超时只在 IDLE 触发
    if not conn.session_sm.is_in(SessionState.IDLE):
        return
    # 只有在已经初始化过时间戳的情况下才进行超时检查
    if conn.last_activity_time > 0.0:
        no_voice_time = time.time() * 1000 - conn.last_activity_time
        close_connection_no_voice_time = int(
            conn.config.get("close_connection_no_voice_time", 120)
        )
        if (
            not conn.close_after_chat
            and no_voice_time > 1000 * close_connection_no_voice_time
        ):
            if not conn.transition_session(
                SessionEvent.IDLE_TIMEOUT, detail="no_voice"
            ):
                return
            conn.close_after_chat = True
            conn.client_abort = False
            end_prompt = conn.config.get("end_prompt", {})
            if end_prompt and end_prompt.get("enable", True) is False:
                conn.logger.bind(tag=TAG).info("结束对话，无需发送结束提示语")
                conn.transition_session(SessionEvent.RESET, detail="idle_close")
                await conn.close()
                return
            prompt = end_prompt.get("prompt")
            if not prompt:
                prompt = "请你以```时间过得真快```未来头，用富有感情、依依不舍的话来结束这场对话吧。！"
            started = await startToChat(conn, prompt)
            # startToChat 早退时可能停在 THINKING，导致无法再 DETECT/LISTEN
            if not started and conn.session_sm.is_in(SessionState.THINKING):
                conn.logger.bind(tag=TAG).warning(
                    "空闲结束语未能启动，RESET 回 IDLE 并关闭连接"
                )
                conn.transition_session(
                    SessionEvent.RESET, detail="idle_close_aborted"
                )
                await conn.close()


async def max_out_size(conn: "ConnectionHandler"):
    # 播放超出最大输出字数的提示
    conn.client_abort = False
    text = "不好意思，我现在有点事情要忙，明天这个时候我们再聊，约好了哦！明天不见不散，拜拜！"
    await send_stt_message(conn, text)
    file_path = "config/assets/max_output_size.wav"
    opus_packets = await audio_to_data(file_path)
    conn.tts.tts_audio_queue.put((SentenceType.LAST, opus_packets, text))
    conn.close_after_chat = True


async def check_bind_device(conn: "ConnectionHandler"):
    if conn.bind_code:
        # 确保bind_code是6位数字
        if len(conn.bind_code) != 6:
            conn.logger.bind(tag=TAG).error(f"无效的绑定码格式: {conn.bind_code}")
            text = "绑定码格式错误，请检查配置。"
            await send_stt_message(conn, text)
            return

        text = f"请登录控制面板，输入{conn.bind_code}，绑定设备。"
        await send_stt_message(conn, text)

        # 播放提示音
        music_path = "config/assets/bind_code.wav"
        opus_packets = await audio_to_data(music_path)
        conn.tts.tts_audio_queue.put((SentenceType.FIRST, opus_packets, text))

        # 逐个播放数字
        for i in range(6):  # 确保只播放6位数字
            try:
                digit = conn.bind_code[i]
                num_path = f"config/assets/bind_code/{digit}.wav"
                num_packets = await audio_to_data(num_path)
                conn.tts.tts_audio_queue.put((SentenceType.MIDDLE, num_packets, None))
            except Exception as e:
                conn.logger.bind(tag=TAG).error(f"播放数字音频失败: {e}")
                continue
        conn.tts.tts_audio_queue.put((SentenceType.LAST, [], None))
    else:
        # 播放未绑定提示
        conn.client_abort = False
        text = f"没有找到该设备的版本信息，请正确配置 OTA地址，然后重新编译固件。"
        await send_stt_message(conn, text)
        music_path = "config/assets/bind_not_found.wav"
        opus_packets = await audio_to_data(music_path)
        conn.tts.tts_audio_queue.put((SentenceType.LAST, opus_packets, text))
