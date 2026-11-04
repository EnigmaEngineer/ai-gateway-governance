import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from gateway.quota import (Admission, CounterStore, Overloaded, QuotaExceeded,
                           TokenBudget)

# Long enough that the prompt estimate is a real share of the reservation.
BODY = '{"mrn": "AB-9931", "dob": "1954-02-11", "note": "seen in clinic"}'


class BarrierStore(CounterStore):
    """Holds every reader until the expected number have arrived.

    The admission race is a window between a read and a write, and a test that
    opens two threads and hopes they land in it is a test that passes most of
    the time. This makes the window as wide as the barrier, so the unserialised
    arm races every run and the serialised one cannot, which is the difference
    being asserted rather than a coincidence.
    """

    def __init__(self, parties, timeout=0.5):
        super().__init__()
        self.barrier = threading.Barrier(parties, timeout=timeout)
        self.broke = 0

    def read(self, tenant):
        # The value comes back BEFORE the barrier. Waiting first synchronises
        # the entry to the read and not the value it returns, so the winner can
        # still write before the loser reads and the race does not happen. This
        # was the first version and it passed about half the time.
        value = super().read(tenant)
        try:
            self.barrier.wait()
        except threading.BrokenBarrierError:
            # One caller reached the read and the other could not, which is
            # what a held lock looks like from in here.
            self.broke += 1
        return value


def _admit_twice(budget):
    def attempt(_):
        try:
            budget.reserve(BODY, 32)
            return True
        except QuotaExceeded:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(attempt, range(2)))


def test_a_budget_that_admits_nothing_is_refused_at_construction():
    with pytest.raises(ValueError, match="admits nothing"):
        TokenBudget("acme", 0)


def test_a_reserve_fraction_outside_the_ceiling_is_refused():
    for bad in (0, -0.5, 1.5):
        with pytest.raises(ValueError, match="share of the ceiling"):
            TokenBudget("acme", 100, reserve_fraction=bad)


def test_the_reservation_covers_the_prompt_and_the_ceiling():
    budget = TokenBudget("acme", 10_000)
    reservation = budget.reserve(BODY, 32)
    assert reservation.estimated == reservation.prompt_estimated + 32
    assert reservation.output_ceiling == 32


def test_a_forecast_reserves_less_than_the_ceiling_does():
    full = TokenBudget("acme", 10_000, reserve_fraction=1.0).reserve(BODY, 32)
    part = TokenBudget("acme", 10_000, reserve_fraction=0.25).reserve(BODY, 32)
    assert part.estimated < full.estimated
    assert part.estimated - part.prompt_estimated == 8


def test_a_forecast_never_reserves_nothing_for_the_output():
    """A fraction small enough to round to zero would reserve the prompt alone."""
    reservation = TokenBudget("acme", 10_000, reserve_fraction=0.01).reserve(BODY, 32)
    assert reservation.estimated > reservation.prompt_estimated


def test_the_budget_refuses_once_the_reservations_fill_it():
    budget = TokenBudget("acme", 60)
    budget.reserve(BODY, 32)
    with pytest.raises(QuotaExceeded, match="tokens and the window has"):
        budget.reserve(BODY, 32)
    assert budget.refused_over_budget == 1


def test_reconciling_replaces_the_reservation_with_the_reported_usage():
    budget = TokenBudget("acme", 10_000)
    reservation = budget.reserve(BODY, 32)
    settlement = budget.reconcile(reservation, 90)
    assert settlement.actual == 90
    assert settlement.shortfall == 90 - reservation.estimated
    assert budget.spent == 90
    assert budget.reserved == 0


def test_a_settlement_inside_the_reservation_records_no_shortfall():
    budget = TokenBudget("acme", 10_000)
    reservation = budget.reserve(BODY, 32)
    settlement = budget.reconcile(reservation, 1)
    assert settlement.shortfall == 0
    assert budget.shortfall_tokens == 0


def test_a_negative_usage_is_refused():
    budget = TokenBudget("acme", 10_000)
    reservation = budget.reserve(BODY, 32)
    with pytest.raises(ValueError, match="not a usage"):
        budget.reconcile(reservation, -1)


def test_releasing_hands_the_whole_estimate_back_and_spends_nothing():
    budget = TokenBudget("acme", 10_000)
    reservation = budget.reserve(BODY, 32)
    budget.release(reservation)
    assert budget.reserved == 0
    assert budget.spent == 0
    assert budget.headroom_returned_tokens == reservation.estimated


def test_the_overshoot_is_never_negative():
    budget = TokenBudget("acme", 10_000)
    budget.reconcile(budget.reserve(BODY, 32), 5)
    assert budget.overshoot == 0


def test_the_overshoot_is_what_the_settlement_put_above_the_budget():
    budget = TokenBudget("acme", 60)
    budget.reconcile(budget.reserve(BODY, 32), 100)
    assert budget.spent == 100
    assert budget.overshoot == 40


def test_without_the_critical_section_two_requests_share_one_reservation():
    """The race, driven rather than hoped for."""
    store = BarrierStore(parties=2)
    budget = TokenBudget("acme", 60, store=store, serialise=False)
    outcomes = _admit_twice(budget)
    assert outcomes == [True, True], (
        "the budget fits one reservation and both callers should have been admitted "
        "without the lock: %r" % outcomes)
    assert store.broke == 0, "the two reads did not meet, so nothing raced"


def test_with_the_critical_section_the_second_request_sees_the_first():
    store = BarrierStore(parties=2)
    budget = TokenBudget("acme", 60, store=store, serialise=True)
    outcomes = _admit_twice(budget)
    assert sorted(outcomes) == [False, True], (
        "the lock should have let exactly one through: %r" % outcomes)
    assert store.broke > 0, (
        "both reads met inside the barrier, so the lock was not holding the second caller out")


def test_the_admission_limit_refuses_rather_than_queues():
    admission = Admission(1)
    with admission:
        with pytest.raises(Overloaded, match="already in detection"):
            with admission:
                pass
    assert admission.refused_overloaded == 1
    assert admission.peak_held == 1


def test_a_refused_admission_does_not_consume_a_slot():
    """A refusal that leaks a slot turns one spike into a permanently full gate."""
    admission = Admission(1)
    for _ in range(3):
        with pytest.raises(Overloaded):
            with admission:
                with admission:
                    pass
    with admission:
        pass
    assert admission.refused_overloaded == 3


def test_a_limit_that_admits_nothing_is_refused_at_construction():
    with pytest.raises(ValueError, match="admits nothing"):
        Admission(0)


def test_the_admission_peak_is_the_most_that_were_ever_held_at_once():
    admission = Admission(4)
    with admission:
        with admission:
            with admission:
                pass
    assert admission.peak_held == 3
