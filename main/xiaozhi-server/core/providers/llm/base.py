from abc import ABC, abstractmethod

from config.logger import setup_logging

TAG = __name__
logger = setup_logging()


class LLMProviderBase(ABC):
    @abstractmethod
    def response(self, session_id, dialogue):
        """LLM response generator"""
        pass

    def response_no_stream(self, system_prompt, user_prompt, **kwargs):
        # 构造对话格式
        dialogue = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        result = ""
        for part in self.response("", dialogue, **kwargs):
            result += part
        return result

    def response_with_functions(self, session_id, dialogue, functions=None):
        """
        Default implementation for function calling (streaming)
        This should be overridden by providers that support function calls

        Returns: generator that yields either text tokens or a special function call token
        """
        # For providers that don't support functions, just return regular response
        for token in self.response(session_id, dialogue):
            yield token, None

    def _iter_response_safe(self, gen, *, stage: str = "llm"):
        """边界强制：adapter 异常一律收敛为 UpstreamError。"""
        from core.utils.resilience import as_upstream_error

        try:
            for item in gen:
                yield item
        except Exception as e:
            raise as_upstream_error(stage, e) from e

    def response_safe(self, session_id, dialogue, **kwargs):
        """response() 的 UpstreamError 边界包装。"""
        return self._iter_response_safe(
            self.response(session_id, dialogue, **kwargs), stage="llm"
        )

    def response_with_functions_safe(self, session_id, dialogue, functions=None):
        """response_with_functions() 的 UpstreamError 边界包装。"""
        return self._iter_response_safe(
            self.response_with_functions(session_id, dialogue, functions),
            stage="llm",
        )
