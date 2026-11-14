# -*- coding: utf-8 -*-
"""Express 窗口超出屏幕（11-13 测试机1 150% 缩放：银行流水报表窗口一部分在屏幕外）：按类处理，操作前把窗口挪回屏内。"""
from __future__ import annotations

from _fake_express import samples
from test_operator_engine import run_docs

from companion.express_operator.desk import fit_rect

WORK = [0, 0, 1280, 680]  # 1920×1080 在 150% 缩放下的逻辑工作区（去掉任务栏）


def test_a_window_inside_the_work_area_is_left_alone():
    assert fit_rect([100, 100, 600, 400], WORK) == []
    assert fit_rect([-8, -8, 1288, 688], WORK, main=True) == []  # 最大化的边框多出几像素不算超出


def test_a_report_window_hanging_off_the_bottom_right_is_moved_back_in_whole():
    assert fit_rect([900, 500, 1500, 900], WORK) == [680, 280, 1280, 680]


def test_a_window_bigger_than_the_screen_is_shrunk_to_the_work_area():
    assert fit_rect([-200, -50, 1600, 1000], WORK) == [0, 0, 1280, 680]


def test_the_main_window_fills_the_work_area_when_it_does_not_fit():
    assert fit_rect([300, 200, 1700, 1100], WORK, main=True) == WORK


def test_a_window_on_a_second_screen_uses_that_screens_work_area():
    assert fit_rect([1300, 100, 2000, 600], [1280, 0, 2560, 680]) == []
    assert fit_rect([2400, 100, 2900, 600], [1280, 0, 2560, 680]) == [2060, 100, 2560, 600]


def test_the_engine_fits_windows_before_opening_a_form_and_before_a_person_screenshot():
    recs, sim, _, _ = run_docs([samples()[0]], with_ai=False, inject={"choice_mid_save": True})
    assert recs[0]["outcome"] == "need_human" and sim.fits >= 2
