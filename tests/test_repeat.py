from datetime import UTC, date
from zoneinfo import ZoneInfo

from chrono_mail.pim.repeat import CustomRepeat, choice_of, custom_of, describe, fitted, repeat_choices, rule_of, same_rule

# --- Version ------------------------------------------------------------
__version__ = "test_repeat.py 4.4.535"

# --- Version history ----------------------------------------------------
# v4.4.535: Initial version (round 4)

TUESDAY = date(2026, 11, 10)
ZONE = ZoneInfo("Europe/Amsterdam")


def test_the_fifth_weekday_of_a_month_is_its_last() -> None:
    choice = repeat_choices(date(2026, 9, 29))[3]
    assert choice.label == "Monthly on the last Tuesday" and choice.rule == "FREQ=MONTHLY;BYDAY=-1TU"


def test_a_rule_means_the_same_with_or_without_what_it_leaves_out() -> None:
    assert same_rule("FREQ=WEEKLY", "FREQ=WEEKLY;INTERVAL=1;BYDAY=TU", TUESDAY)
    assert same_rule("FREQ=WEEKLY;BYDAY=FR,MO", "BYDAY=MO,FR;FREQ=WEEKLY", TUESDAY)
    assert not same_rule("FREQ=WEEKLY", "FREQ=WEEKLY;BYDAY=WE", TUESDAY)
    assert choice_of("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", TUESDAY) == 5 and choice_of("FREQ=MONTHLY", TUESDAY) == -1


def test_the_last_day_is_a_date_for_all_day_and_the_end_of_the_day_in_utc_for_a_time() -> None:
    custom = CustomRepeat("DAILY", 1, (), date(2026, 12, 1))
    assert rule_of(custom, "", TUESDAY, True, ZONE) == "FREQ=DAILY;UNTIL=20261201"
    timed = rule_of(custom, "", TUESDAY, False, ZONE)
    assert timed == "FREQ=DAILY;UNTIL=20261201T225959Z"
    assert custom_of(timed, TUESDAY, ZONE).until == date(2026, 12, 1)
    assert fitted(timed, True, ZONE) == "FREQ=DAILY;UNTIL=20261201" and fitted("FREQ=DAILY;UNTIL=20261201", False, ZONE) == timed


def test_what_the_custom_window_does_not_show_stays_while_the_unit_stays() -> None:
    before = "FREQ=MONTHLY;BYDAY=2TU;BYSETPOS=1"
    assert rule_of(CustomRepeat("MONTHLY", 2), before, TUESDAY, False, UTC) == "FREQ=MONTHLY;INTERVAL=2;BYDAY=2TU;BYSETPOS=1"
    assert rule_of(CustomRepeat("DAILY", 2), before, TUESDAY, False, UTC) == "FREQ=DAILY;INTERVAL=2"


def test_a_weekly_rule_without_days_ticked_repeats_on_the_day_of_the_event() -> None:
    assert rule_of(CustomRepeat("WEEKLY", 2), "", TUESDAY, False, UTC) == "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU"


def test_odd_values_of_a_rule_from_elsewhere_never_stop_the_window() -> None:
    custom = custom_of("FREQ=SECONDLY;INTERVAL=x²;COUNT=²;UNTIL=soon", TUESDAY, UTC)
    assert custom == CustomRepeat("WEEKLY", 1, ("TU",), None, None)
    assert custom_of(f"FREQ=DAILY;INTERVAL={'9' * 5000};COUNT={'9' * 5000}", TUESDAY, UTC) == CustomRepeat("DAILY", 1, ("TU",), None, None)
    assert describe("FREQ=MONTHLY;BYMONTHDAY=1,15", TUESDAY, UTC) == "Every month (BYMONTHDAY=1,15)"
