"""Cowork 任务管理的看板权限矩阵（`work.contracts.board_rights` / `member_changes`），不依赖框架与 IO。

用户口径 2026-09-29：私有 / 团队可见两档（团队可见 = 大家能看、成员才能改，默认看板人人能改）；看板管理员、成员、观察者三种角色；Cowork 管理员管所有看板；
谁能评论、谁能加减成员由看板管理员设置。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "pearnly_cowork"))

from pearnly_cowork.work import contracts  # noqa: E402
from pearnly_cowork.work.contracts import Invalid  # noqa: E402

ALL = set(contracts.RIGHTS)


def rights(**kwargs):
    base = {"visibility": "private", "team_user": True, "cowork_admin": False, "commenting": "members",
            "member_management": "admins"}
    return set(contracts.board_rights(**{**base, **kwargs}))


class BoardRightsTest(unittest.TestCase):
    def test_cowork_admin_manages_every_board_including_private(self):
        for visibility in contracts.VISIBILITIES:
            self.assertEqual(rights(cowork_admin=True, visibility=visibility), ALL)
        self.assertEqual(rights(cowork_admin=True, commenting="disabled"), ALL)

    def test_board_admin_has_every_right(self):
        self.assertEqual(rights(role="admin"), ALL)
        self.assertEqual(rights(role="admin", commenting="disabled"), ALL)

    def test_member_edits_but_does_not_manage(self):
        self.assertEqual(rights(role="member"), {"view", "edit", "comment"})
        self.assertEqual(rights(role="member", member_management="members"), {"view", "edit", "comment", "members"})
        self.assertEqual(rights(role="member", commenting="disabled"), {"view", "edit"})
        self.assertEqual(rights(role="member", commenting="observers"), {"view", "edit", "comment"})

    def test_observer_only_views_and_comments_when_allowed(self):
        self.assertEqual(rights(role="observer"), {"view"})
        self.assertEqual(rights(role="observer", commenting="observers"), {"view", "comment"})
        self.assertEqual(rights(role="observer", commenting="disabled"), {"view"})
        # 团队可见的板上被设成观察者：按观察者算，不按"团队成员都能改"。
        self.assertEqual(rights(role="observer", visibility="team", member_management="members"), {"view"})

    def test_team_board_non_member_only_views(self):
        # 用户 2026-09-29 定 B：团队可见的板，没加进来的人只能看，评论按观察者算。
        self.assertEqual(rights(visibility="team"), {"view"})
        self.assertEqual(rights(visibility="team", commenting="observers"), {"view", "comment"})
        self.assertEqual(rights(visibility="team", member_management="members"), {"view"})

    def test_default_board_stays_editable_for_the_whole_team(self):
        self.assertEqual(rights(visibility="team", is_default=True), {"view", "edit", "comment"})
        self.assertEqual(rights(visibility="team", is_default=True, commenting="disabled"), {"view", "edit"})
        self.assertEqual(rights(visibility="team", is_default=True, member_management="members"),
                         {"view", "edit", "comment"})
        # 默认看板被改成私有：没加进来的人就看不到了；被设成观察者的人照样只读。
        self.assertEqual(rights(visibility="private", is_default=True), set())
        self.assertEqual(rights(role="observer", visibility="team", is_default=True), {"view"})

    def test_private_board_non_member_gets_nothing(self):
        self.assertEqual(rights(), set())
        self.assertEqual(rights(member_management="members", commenting="observers"), set())

    def test_without_task_management_nothing_on_team_board(self):
        self.assertEqual(rights(visibility="team", team_user=False), set())
        self.assertEqual(rights(visibility="team", team_user=False, is_default=True), set())


class MemberChangesTest(unittest.TestCase):
    CURRENT = {"a@x": "admin", "m@x": "member", "o@x": "observer"}

    def test_admin_can_add_remove_and_change_roles(self):
        wanted = {"a@x": "admin", "m@x": "observer", "n@x": "admin"}
        changes = contracts.member_changes(self.CURRENT, wanted, actor="a@x", manage=True, can_members=True)
        self.assertEqual(changes, {"add": {"n@x": "admin"}, "remove": ["o@x"], "change": {"m@x": "observer"}})

    def test_members_right_adds_and_removes_plain_members_only(self):
        wanted = {**self.CURRENT, "n@x": "member"}
        del wanted["o@x"]
        changes = contracts.member_changes(self.CURRENT, wanted, actor="m@x", manage=False, can_members=True)
        self.assertEqual(changes["add"], {"n@x": "member"})
        for wanted in ({**self.CURRENT, "n@x": "admin"},                 # 加管理员
                       {**self.CURRENT, "o@x": "member"},                # 改角色
                       {"m@x": "member", "o@x": "observer"}):            # 移除管理员
            with self.subTest(wanted=wanted), self.assertRaisesRegex(Invalid, "board_rights"):
                contracts.member_changes(self.CURRENT, wanted, actor="m@x", manage=False, can_members=True)

    def test_without_members_right_only_leaving_is_allowed(self):
        with self.assertRaisesRegex(Invalid, "board_rights"):
            contracts.member_changes(self.CURRENT, {**self.CURRENT, "n@x": "member"}, actor="o@x", manage=False,
                                     can_members=False)
        leave = {"a@x": "admin", "m@x": "member"}
        self.assertEqual(contracts.member_changes(self.CURRENT, leave, actor="o@x", manage=False,
                                                  can_members=False)["remove"], ["o@x"])

    def test_last_admin_cannot_be_removed_or_demoted(self):
        for wanted in ({"m@x": "member", "o@x": "observer"}, {**self.CURRENT, "a@x": "member"}):
            with self.subTest(wanted=wanted), self.assertRaisesRegex(Invalid, "last_admin"):
                contracts.member_changes(self.CURRENT, wanted, actor="a@x", manage=True, can_members=True)
        # 默认看板本来就没有显式管理员：加减成员不受这条限制。
        self.assertEqual(contracts.member_changes({}, {"m@x": "observer"}, actor="c@x", manage=True,
                                                  can_members=True)["add"], {"m@x": "observer"})

    def test_bad_shapes_are_rejected(self):
        for wanted in (None, [], {"": "member"}, {"x@x": "owner"}, {"x@x": None}):
            with self.subTest(wanted=wanted), self.assertRaises(Invalid):
                contracts.member_changes({}, wanted, actor="a@x", manage=True, can_members=True)


if __name__ == "__main__":
    unittest.main()
