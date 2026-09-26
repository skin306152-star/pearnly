# -*- coding: utf-8 -*-
"""单实例守卫:同名锁只能被持有一次,释放后可重新获取。"""
from companion import single_instance


def test_second_acquire_blocked_until_release():
    name = "PearnlyTest_SingleInstance_A"
    g1 = single_instance.acquire(name)
    assert g1 is not None
    try:
        # 同名第二次获取被拒(模拟第二个实例启动)。
        assert single_instance.acquire(name) is None
    finally:
        g1.release()
    # 释放后可再次获取(模拟首个实例退出后重启)。
    g2 = single_instance.acquire(name)
    assert g2 is not None
    g2.release()


def test_distinct_names_independent():
    a = single_instance.acquire("PearnlyTest_SingleInstance_B")
    b = single_instance.acquire("PearnlyTest_SingleInstance_C")
    assert a is not None and b is not None
    a.release()
    b.release()


def test_release_is_idempotent():
    g = single_instance.acquire("PearnlyTest_SingleInstance_D")
    assert g is not None
    g.release()
    g.release()  # 二次释放不报错
