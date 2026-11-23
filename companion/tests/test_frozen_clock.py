# -*- coding: utf-8 -*-
"""``frozen_clock`` 夹具真的钉住了写入代码看到的「现在」（两本副本逐字节比的测试靠它，11-23）。"""

from companion import dbf_schema as S
from companion import dbf_stock


def test_the_writers_see_one_moment(frozen_clock):
    first = S._dt.datetime.now()
    assert first == frozen_clock and S._dt.datetime.now() == first
    assert dbf_stock._dt.datetime.now() == frozen_clock and dbf_stock._dt.date.today() == frozen_clock.date()


def test_journal_lines_get_the_same_time_stamp_twice(frozen_clock):
    class _Table:
        field_names = ["CHGDAT", "CHGTIM"]

    one, two = S.journal_stamp(_Table(), {}), S.journal_stamp(_Table(), {})
    assert one == two and one["CHGTIM"] == frozen_clock.strftime("%H%M")
