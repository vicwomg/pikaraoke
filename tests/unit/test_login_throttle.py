"""Tests for the login brute-force throttle."""

from pikaraoke.lib.login_throttle import LoginThrottle


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_throttle(clock: FakeClock) -> LoginThrottle:
    return LoginThrottle(free_attempts=3, base_cooldown=10.0, max_cooldown=40.0, clock=clock)


class TestLoginThrottle:
    def test_free_attempts_do_not_lock(self):
        throttle = make_throttle(FakeClock())

        for _ in range(3):
            assert throttle.record_failure("a") is None
        assert throttle.retry_after("a") is None

    def test_the_attempt_past_the_free_ones_locks(self):
        throttle = make_throttle(FakeClock())

        for _ in range(3):
            throttle.record_failure("a")

        assert throttle.record_failure("a") == 10.0
        assert throttle.retry_after("a") == 10.0

    def test_the_cooldown_doubles_each_further_failure(self):
        clock = FakeClock()
        throttle = make_throttle(clock)
        for _ in range(3):
            throttle.record_failure("a")

        assert throttle.record_failure("a") == 10.0
        clock.advance(10.0)
        assert throttle.record_failure("a") == 20.0
        clock.advance(20.0)
        assert throttle.record_failure("a") == 40.0

    def test_the_cooldown_is_capped(self):
        clock = FakeClock()
        throttle = make_throttle(clock)

        for _ in range(10):
            cooldown = throttle.record_failure("a")
            if cooldown:
                clock.advance(cooldown)

        assert throttle.record_failure("a") == 40.0

    def test_a_lock_expires(self):
        clock = FakeClock()
        throttle = make_throttle(clock)
        for _ in range(4):
            throttle.record_failure("a")

        clock.advance(10.0)

        assert throttle.retry_after("a") is None

    def test_success_clears_the_count(self):
        throttle = make_throttle(FakeClock())
        for _ in range(3):
            throttle.record_failure("a")

        throttle.record_success("a")

        assert throttle.retry_after("a") is None
        for _ in range(3):
            assert throttle.record_failure("a") is None

    def test_callers_are_independent(self):
        throttle = make_throttle(FakeClock())
        for _ in range(4):
            throttle.record_failure("a")

        assert throttle.retry_after("a") is not None
        assert throttle.retry_after("b") is None
