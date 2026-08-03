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
