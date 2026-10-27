from cagr_data.orchestration.backfill import wait_until


def test_wait_until_polls_until_ready() -> None:
    answers = iter([False, False, True])
    slept: list[float] = []
    assert wait_until(lambda: next(answers), poll_s=5, limit_s=60, sleep=slept.append)
    assert slept == [5, 5]


def test_wait_until_gives_up_at_its_limit() -> None:
    slept: list[float] = []
    assert not wait_until(lambda: False, poll_s=5, limit_s=10, sleep=slept.append)
    assert slept == [5, 5]
