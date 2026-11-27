from src.warn_throttle import WarnThrottle


def test_allows_once_per_key_per_interval() -> None:
    now = [0.0]
    throttle = WarnThrottle(60, clock=lambda: now[0])
    assert throttle.allow("a") is True
    assert throttle.allow("a") is False
    assert throttle.allow("b") is True  # keys are independent
    now[0] = 59.9
    assert throttle.allow("a") is False
    now[0] = 60.0
    assert throttle.allow("a") is True
