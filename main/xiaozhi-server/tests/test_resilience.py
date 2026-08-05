#!/usr/bin/env python3
"""resilience 单测（不依赖运行中的 WS 服务）。"""

from __future__ import annotations

import time
import unittest

from core.utils.circuit_store import LocalCircuitStore, reset_circuit_stores_for_tests
from core.utils.resilience import (
    CircuitBreaker,
    RoundBudget,
    UpstreamError,
    UpstreamKind,
    as_upstream_error,
    classify_exception,
    ensure_non_empty,
    get_fallback_text,
    get_provider_policy,
    get_resilience_settings,
    maybe_chaos_fail,
    next_with_timeout,
)


class TestResilienceSettings(unittest.TestCase):
    def test_budget_defaults_not_8s_full_round(self):
        s = get_resilience_settings({})
        self.assertGreaterEqual(s.llm_ttfb_deadline_seconds, 15)
        self.assertGreaterEqual(s.round_deadline_seconds, 60)
        self.assertLess(s.llm_ttfb_deadline_seconds, s.round_deadline_seconds)

    def test_provider_policy_override(self):
        cfg = {
            "server": {
                "resilience": {
                    "providers": {"llm": {"ChatGLMLLM": {"timeout_seconds": 77}}}
                }
            }
        }
        p = get_provider_policy(cfg, "llm", "ChatGLMLLM")
        self.assertEqual(p["timeout_seconds"], 77)

    def test_chaos_defaults_off(self):
        s = get_resilience_settings({})
        self.assertFalse(s.chaos_enabled)
        maybe_chaos_fail("llm", {})  # must not raise

    def test_redis_settings_default_off(self):
        s = get_resilience_settings({})
        self.assertFalse(s.circuit_redis_enabled)
        s2 = get_resilience_settings(
            {
                "server": {
                    "resilience": {
                        "redis": {"enabled": True, "host": "10.0.0.1", "db": 2}
                    }
                }
            }
        )
        self.assertTrue(s2.circuit_redis_enabled)
        self.assertEqual(s2.circuit_redis_host, "10.0.0.1")
        self.assertEqual(s2.circuit_redis_db, 2)


class TestRoundBudget(unittest.TestCase):
    def test_ttfb_and_round(self):
        s = get_resilience_settings(
            {
                "server": {
                    "resilience": {
                        "llm_ttfb_deadline_seconds": 25,
                        "round_deadline_seconds": 120,
                    }
                }
            }
        )
        b = RoundBudget.start(s)
        self.assertIsNotNone(b.ttfb_remaining())
        b.mark_first_token()
        self.assertIsNone(b.ttfb_remaining())

        expired = RoundBudget(
            started_at=time.monotonic() - 1,
            llm_ttfb_deadline_seconds=0.01,
            round_deadline_seconds=100,
        )
        with self.assertRaises(UpstreamError) as ctx:
            expired.ensure_ttfb()
        self.assertEqual(ctx.exception.kind, UpstreamKind.TIMEOUT)


class TestCircuitBreaker(unittest.TestCase):
    def setUp(self):
        reset_circuit_stores_for_tests()

    def test_open_and_recover(self):
        store = LocalCircuitStore()
        c = CircuitBreaker(
            "unit:test", failure_threshold=2, open_seconds=30, store=store
        )
        c.record_failure()
        self.assertEqual(c.state(), "closed")
        c.record_failure()
        self.assertEqual(c.state(), "open")
        self.assertFalse(c.allow())
        c.record_success()
        self.assertEqual(c.state(), "closed")

    def test_shared_store_across_breakers(self):
        store = LocalCircuitStore()
        a = CircuitBreaker(
            "shared:asr", failure_threshold=2, open_seconds=60, store=store
        )
        b = CircuitBreaker(
            "shared:asr", failure_threshold=2, open_seconds=60, store=store
        )
        a.record_failure()
        a.record_failure()
        self.assertFalse(b.allow())
        b.record_success()
        self.assertTrue(a.allow())


class TestUpstreamHelpers(unittest.TestCase):
    def test_as_upstream_error(self):
        err = as_upstream_error("asr", TimeoutError("x"))
        self.assertEqual(err.kind, UpstreamKind.TIMEOUT)
        self.assertTrue(err.retryable)

    def test_ensure_non_empty(self):
        with self.assertRaises(UpstreamError) as ctx:
            ensure_non_empty("asr", "")
        self.assertEqual(ctx.exception.kind, UpstreamKind.EMPTY)


class TestChaos(unittest.TestCase):
    def test_chaos_always_fail(self):
        cfg = {
            "server": {
                "resilience": {
                    "chaos": {"enabled": True, "llm_fail_rate": 1.0}
                }
            }
        }
        with self.assertRaises(UpstreamError) as ctx:
            maybe_chaos_fail("llm", cfg)
        self.assertEqual(ctx.exception.kind, UpstreamKind.UNAVAILABLE)
        self.assertIn("chaos", str(ctx.exception).lower())


class TestInFlightLimiter(unittest.TestCase):
    def setUp(self):
        from core.utils.resilience import reset_inflight_for_tests

        reset_inflight_for_tests()

    def tearDown(self):
        from core.utils.resilience import reset_inflight_for_tests

        reset_inflight_for_tests()

    def test_acquire_release_and_limit(self):
        from core.utils.resilience import (
            get_inflight_count,
            release_inflight,
            try_acquire_inflight,
        )

        self.assertTrue(try_acquire_inflight("chat", 2))
        self.assertTrue(try_acquire_inflight("chat", 2))
        self.assertFalse(try_acquire_inflight("chat", 2))
        self.assertEqual(get_inflight_count("chat"), 2)
        release_inflight("chat")
        self.assertTrue(try_acquire_inflight("chat", 2))
        self.assertEqual(get_inflight_count("chat"), 2)

    def test_limit_zero_unlimited(self):
        from core.utils.resilience import try_acquire_inflight

        for _ in range(5):
            self.assertTrue(try_acquire_inflight("llm", 0))

    def test_check_system_overload_inflight(self):
        from core.utils.resilience import (
            check_system_overload,
            release_inflight,
            try_acquire_inflight,
        )

        class _Dummy:
            config = {
                "server": {
                    "resilience": {
                        "overload": {
                            "enabled": True,
                            "max_concurrent_chats": 1,
                            "max_concurrent_llm": 99,
                            "tts_text_queue_threshold": 9999,
                            "tts_audio_queue_threshold": 9999,
                            "report_queue_usage_threshold": 1.1,
                        }
                    }
                }
            }
            tts = None
            report_queue = None
            server = None

        self.assertIsNone(check_system_overload(_Dummy()))
        self.assertTrue(try_acquire_inflight("chat", 1))
        reason = check_system_overload(_Dummy())
        self.assertIsNotNone(reason)
        self.assertIn("in_flight_chats", reason)
        release_inflight("chat")
        self.assertIsNone(check_system_overload(_Dummy()))

    def test_settings_no_connection_usage(self):
        s = get_resilience_settings({})
        self.assertEqual(s.overload_max_concurrent_chats, 80)
        self.assertEqual(s.overload_max_concurrent_llm, 80)
        self.assertFalse(hasattr(s, "overload_connection_usage_threshold"))
        self.assertEqual(s.overload_tts_text_queue_maxsize, 80)
        self.assertEqual(s.overload_tts_audio_queue_maxsize, 120)
        self.assertEqual(s.overload_asr_audio_queue_maxsize, 200)

    def test_overload_message_phrase(self):
        s = get_resilience_settings(
            {
                "server": {
                    "resilience": {
                        "overload": {
                            "enabled": True,
                            "max_concurrent_chats": 10,
                            "message": "忙不过来啦",
                        },
                    }
                }
            }
        )
        self.assertEqual(s.overload_max_concurrent_chats, 10)
        self.assertEqual(s.phrases.get("overload"), "忙不过来啦")

    def test_maxsize_clamped_to_threshold(self):
        s = get_resilience_settings(
            {
                "server": {
                    "resilience": {
                        "overload": {
                            "tts_text_queue_threshold": 100,
                            "tts_text_queue_maxsize": 40,
                        }
                    }
                }
            }
        )
        self.assertEqual(s.overload_tts_text_queue_threshold, 100)
        self.assertEqual(s.overload_tts_text_queue_maxsize, 100)


class TestDroppingQueue(unittest.TestCase):
    def test_drops_oldest_when_full(self):
        from core.utils.bounded_queue import DroppingQueue

        q = DroppingQueue(maxsize=2, name="test")
        q.put("a")
        q.put("b")
        q.put("c")
        self.assertEqual(q.qsize(), 2)
        self.assertEqual(q.get_nowait(), "b")
        self.assertEqual(q.get_nowait(), "c")
        self.assertGreaterEqual(q.dropped, 1)


class TestSpeakDegradationOverload(unittest.TestCase):
    def test_tts_queue_overload_skips_enqueue(self):
        from core.utils.resilience import speak_degradation, UpstreamKind
        from core.utils.bounded_queue import DroppingQueue

        puts = []

        class _Q(DroppingQueue):
            def put(self, item, block=True, timeout=None):
                puts.append(item)
                return super().put(item, block=block, timeout=timeout)

        class _TTS:
            tts_text_queue = _Q(maxsize=8, name="tts_text")

            def store_tts_text(self, *a, **k):
                pass

        class _Conn:
            config = {"server": {"resilience": {"enabled": True}}}
            stop_event = None
            client_abort = False
            tts = _TTS()
            sentence_id = "sid"
            dialogue = None

        speak_degradation(
            _Conn(),
            "overload",
            UpstreamKind.OVERLOAD,
            overload_reason="tts_text_queue=80",
        )
        self.assertEqual(puts, [])


class TestAudioRateOverload(unittest.TestCase):
    def test_check_includes_audio_rate_controller(self):
        from core.utils.resilience import check_system_overload, reset_inflight_for_tests
        from core.utils.audioRateController import AudioRateController

        reset_inflight_for_tests()
        rc = AudioRateController(60)
        for i in range(80):
            rc.add_audio(b"x")

        class _Dummy:
            config = {
                "server": {
                    "resilience": {
                        "overload": {
                            "enabled": True,
                            "max_concurrent_chats": 0,
                            "max_concurrent_llm": 0,
                            "tts_text_queue_threshold": 9999,
                            "tts_audio_queue_threshold": 9999,
                            "audio_rate_queue_threshold": 80,
                            "report_queue_usage_threshold": 1.1,
                        }
                    }
                }
            }
            tts = None
            report_queue = None
            audio_rate_controller = rc

        reason = check_system_overload(_Dummy())
        self.assertIsNotNone(reason)
        self.assertIn("audio_rate_queue", reason)


class TestQueueDepthAggregate(unittest.TestCase):
    def test_multi_conn_sums(self):
        from core.utils import metrics as m

        m._QUEUE_DEPTH_BY_CONN.clear()
        m._ENABLED = False  # gauge may be None; still exercise aggregator
        m.set_queue_depth("tts_audio", 3, conn_id="a")
        m.set_queue_depth("tts_audio", 5, conn_id="b")
        total = sum(
            v for (c, q), v in m._QUEUE_DEPTH_BY_CONN.items() if q == "tts_audio"
        )
        self.assertEqual(total, 8)
        m.clear_conn_queue_depths("a")
        total = sum(
            v for (c, q), v in m._QUEUE_DEPTH_BY_CONN.items() if q == "tts_audio"
        )
        self.assertEqual(total, 5)


class TestHelpers(unittest.TestCase):
    def test_classify_timeout(self):
        self.assertEqual(classify_exception(TimeoutError("x")), UpstreamKind.TIMEOUT)

    def test_fallback_overload_phrase(self):
        text = get_fallback_text({}, "overload", UpstreamKind.OVERLOAD)
        self.assertTrue(len(text) > 0)

    def test_next_with_timeout(self):
        self.assertEqual(next_with_timeout(iter([1, 2]), 1.0), 1)


if __name__ == "__main__":
    unittest.main()
