import pytest

from cagr_data.orchestration.backfill import main, wait_until


def test_wait_until_polls_until_ready() -> None:
    answers = iter([False, False, True])
    slept: list[float] = []
    assert wait_until(lambda: next(answers), poll_s=5, limit_s=60, sleep=slept.append)
    assert slept == [5, 5]


def test_wait_until_gives_up_at_its_limit() -> None:
    slept: list[float] = []
    assert not wait_until(lambda: False, poll_s=5, limit_s=10, sleep=slept.append)
    assert slept == [5, 5]


@pytest.mark.parametrize("rate", ["0", "1.5"])
def test_rate_must_stay_polite(rate: str) -> None:
    with pytest.raises(SystemExit):
        main(["2010-01-01", "2010-12-31", "--rate", rate])
