"""有界丢弃队列：满时丢最旧项再入队，永不阻塞生产者。"""

from __future__ import annotations

import queue
from typing import Any, Callable, Optional


class DroppingQueue(queue.Queue):
    """maxsize>0 时 put/put_nowait 永不阻塞：满则丢弃队头再写入。

    注意：stdlib Queue.put_nowait 会调用 put(block=False)，因此只覆盖 put，
    避免 put ↔ put_nowait 递归。
    """

    def __init__(
        self,
        maxsize: int = 0,
        *,
        name: str = "",
        conn_id_getter: Optional[Callable[[], Optional[str]]] = None,
    ):
        super().__init__(maxsize=max(0, int(maxsize)))
        self.name = name or "queue"
        self.dropped = 0
        self._conn_id_getter = conn_id_getter

    def put(self, item: Any, block: bool = True, timeout: Optional[float] = None) -> None:
        if self.maxsize <= 0:
            return super().put(item, block=block, timeout=timeout)
        # 过载场景下不允许阻塞会话线程
        while True:
            try:
                super().put(item, block=False)
                self._observe_depth()
                return
            except queue.Full:
                try:
                    self.get_nowait()
                except queue.Empty:
                    continue
                self._record_drop()

    def _record_drop(self) -> None:
        self.dropped += 1
        try:
            from core.utils import metrics as metrics_mod

            metrics_mod.observe_queue_dropped(self.name)
        except Exception:
            pass

    def _observe_depth(self) -> None:
        try:
            from core.utils import metrics as metrics_mod

            conn_id = None
            if self._conn_id_getter is not None:
                try:
                    conn_id = self._conn_id_getter()
                except Exception:
                    conn_id = None
            metrics_mod.set_queue_depth(self.name, self.qsize(), conn_id=conn_id)
        except Exception:
            pass
