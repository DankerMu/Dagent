"""Calendar triggers reject out-of-range recurrence days at the API schema seam."""

import pytest
from pydantic import ValidationError

from xagent.web.services.trigger_providers.schemas import parse_trigger_config


def test_monthly_schedule_rejects_zero_day_instead_of_scheduling_previous_month():
    with pytest.raises(ValidationError, match="day_of_month must be between 1 and 31"):
        parse_trigger_config(
            "scheduled",
            {"recurrence": "monthly", "day_of_month": 0, "time_of_day": "09:00"},
        )
