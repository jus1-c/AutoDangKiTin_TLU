import unittest

from src.config import Config
from src.models.course import Course
from src.models.user import User
from src.services.transfer_service import TransferService


class FakeRegister:
    def __init__(self, burst_results=None, drop_result=(True, 0), drop_results=None):
        self.burst_results = list(burst_results or [])
        self.drop_result = drop_result
        self.drop_results = list(drop_results or [])
        self.burst_calls = 0
        self.drop_calls = 0
        self.burst_codes = []
        self.drop_codes = []

    async def _burst_request(self, url, data, count):
        self.burst_calls += 1
        self.burst_codes.append(data.get("code"))
        if self.burst_results:
            return self.burst_results.pop(0)
        return False, -6

    async def drop_class(self, user, period_id, data):
        self.drop_calls += 1
        self.drop_codes.append(data.get("code"))
        if self.drop_results:
            return self.drop_results.pop(0)
        return self.drop_result


def make_course(code, subject_id=1, is_full=False, current=1, max_students=10):
    return Course(data={
        "code": code,
        "displayName": code,
        "subjectId": subject_id,
        "isFullClass": is_full,
        "numberStudent": current,
        "maxStudent": max_students,
        "timetables": [],
    })


def make_user(username):
    return User(username=username, student_id=username, semester_id=1)


class TransferServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._old_config = {
            "TRANSFER_BETA_RETRY_COUNT": Config.TRANSFER_BETA_RETRY_COUNT,
            "TRANSFER_ROLLBACK_RETRY_COUNT": Config.TRANSFER_ROLLBACK_RETRY_COUNT,
            "TRANSFER_ROLLBACK_RETRY_DELAY": Config.TRANSFER_ROLLBACK_RETRY_DELAY,
        }
        Config.TRANSFER_BETA_RETRY_COUNT = 1
        Config.TRANSFER_ROLLBACK_RETRY_COUNT = 2
        Config.TRANSFER_ROLLBACK_RETRY_DELAY = 0

    def tearDown(self):
        for key, value in self._old_config.items():
            setattr(Config, key, value)

    async def test_simple_give_open_slot_registers_before_drop(self):
        service = TransferService()
        giver = FakeRegister(drop_result=(True, 0))
        receiver = FakeRegister(burst_results=[(True, 0)])
        course = make_course("C1", is_full=False, current=5, max_students=10)

        result = await service.simple_give(
            giver, make_user("giver"), 1,
            receiver, make_user("receiver"), 1,
            course, lead=0, timeout=0.1, count=1,
            on_log=lambda _msg: None,
        )

        self.assertTrue(result["safe_first"])
        self.assertTrue(result["grabbed"])
        self.assertTrue(result["dropped"])
        self.assertEqual(receiver.burst_calls, 1)
        self.assertEqual(giver.drop_calls, 1)

    async def test_simple_give_open_slot_does_not_drop_on_hard_fail(self):
        service = TransferService()
        giver = FakeRegister(drop_result=(True, 0))
        receiver = FakeRegister(burst_results=[(False, -4)])
        course = make_course("C1", is_full=False, current=5, max_students=10)

        result = await service.simple_give(
            giver, make_user("giver"), 1,
            receiver, make_user("receiver"), 1,
            course, lead=0, timeout=0.1, count=1,
            on_log=lambda _msg: None,
        )

        self.assertTrue(result["safe_first"])
        self.assertFalse(result["grabbed"])
        self.assertFalse(result["dropped"])
        self.assertEqual(result["grab_status"], -4)
        self.assertEqual(giver.drop_calls, 0)

    def test_plan_pairs_same_subject_swap(self):
        x = make_course("X", subject_id=10)
        y = make_course("Y", subject_id=10)

        plan = TransferService.plan([x], [x], [y], [y])

        self.assertEqual(plan["beta_pairs"], [(x, y)])
        self.assertEqual(plan["simple_a_to_b"], [])
        self.assertEqual(plan["simple_b_to_a"], [])
        self.assertEqual(plan["errors"], [])

    def test_plan_errors_when_receiver_keeps_same_subject(self):
        x = make_course("X", subject_id=10)
        kept = make_course("KEPT", subject_id=10)

        plan = TransferService.plan([x], [x], [], [kept])

        self.assertEqual(plan["beta_pairs"], [])
        self.assertEqual(plan["simple_a_to_b"], [x])
        self.assertTrue(plan["errors"])
        self.assertIn("cùng môn", plan["errors"][0])

    async def test_same_subject_swap_spins_through_minus_four(self):
        service = TransferService()
        x = make_course("X", subject_id=10)
        y = make_course("Y", subject_id=10)
        a_reg = FakeRegister(burst_results=[(False, -4), (True, 0)])
        b_reg = FakeRegister(burst_results=[(False, -4), (True, 0)])

        result = await service.swap_same_slot(
            a_reg, make_user("a"), 1, x,
            b_reg, make_user("b"), 1, y,
            lead=0, timeout=0.5, count=1,
            on_log=lambda _msg: None,
        )

        self.assertTrue(result["a_dropped_x"])
        self.assertTrue(result["b_dropped_y"])
        self.assertTrue(result["a_grabbed_y"])
        self.assertTrue(result["b_grabbed_x"])
        self.assertGreaterEqual(a_reg.burst_calls, 2)
        self.assertGreaterEqual(b_reg.burst_calls, 2)

    async def test_beta_partial_drop_rolls_back_then_retries(self):
        service = TransferService()
        x = make_course("X", subject_id=10)
        y = make_course("Y", subject_id=10)
        a_reg = FakeRegister(
            burst_results=[(False, -4), (True, 0), (False, -4), (True, 0)],
            drop_results=[(True, 0), (True, 0)],
        )
        b_reg = FakeRegister(
            burst_results=[(False, -4), (False, -4), (True, 0)],
            drop_results=[(False, -99), (True, 0)],
        )

        result = await service.swap_same_slot(
            a_reg, make_user("a"), 1, x,
            b_reg, make_user("b"), 1, y,
            lead=0, timeout=0.5, count=1,
            on_log=lambda _msg: None,
        )

        self.assertEqual(result["attempt"], 2)
        self.assertTrue(result["a_dropped_x"])
        self.assertTrue(result["b_dropped_y"])
        self.assertTrue(result["a_grabbed_y"])
        self.assertTrue(result["b_grabbed_x"])
        self.assertIn("X", a_reg.burst_codes)  # rollback A về lớp cũ
        self.assertEqual(a_reg.drop_calls, 2)
        self.assertEqual(b_reg.drop_calls, 2)

    async def test_beta_partial_drop_stops_when_rollback_fails(self):
        service = TransferService()
        x = make_course("X", subject_id=10)
        y = make_course("Y", subject_id=10)
        Config.TRANSFER_BETA_RETRY_COUNT = 2
        Config.TRANSFER_ROLLBACK_RETRY_COUNT = 1
        a_reg = FakeRegister(
            burst_results=[(False, -4), (False, -6)],
            drop_results=[(True, 0)],
        )
        b_reg = FakeRegister(
            burst_results=[(False, -4)],
            drop_results=[(False, -99)],
        )

        result = await service.swap_same_slot(
            a_reg, make_user("a"), 1, x,
            b_reg, make_user("b"), 1, y,
            lead=0, timeout=0.5, count=1,
            on_log=lambda _msg: None,
        )

        self.assertEqual(result["attempt"], 1)
        self.assertFalse(result["a_rollback_x"])
        self.assertEqual(result["a_rollback_status"], -6)
        self.assertEqual(a_reg.drop_calls, 1)
        self.assertEqual(b_reg.drop_calls, 1)

    async def test_beta_retries_when_both_drops_fail(self):
        service = TransferService()
        x = make_course("X", subject_id=10)
        y = make_course("Y", subject_id=10)
        a_reg = FakeRegister(
            burst_results=[(False, -4), (False, -4), (True, 0)],
            drop_results=[(False, -99), (True, 0)],
        )
        b_reg = FakeRegister(
            burst_results=[(False, -4), (False, -4), (True, 0)],
            drop_results=[(False, -99), (True, 0)],
        )

        result = await service.swap_same_slot(
            a_reg, make_user("a"), 1, x,
            b_reg, make_user("b"), 1, y,
            lead=0, timeout=0.5, count=1,
            on_log=lambda _msg: None,
        )

        self.assertEqual(result["attempt"], 2)
        self.assertTrue(result["a_dropped_x"])
        self.assertTrue(result["b_dropped_y"])
        self.assertTrue(result["a_grabbed_y"])
        self.assertTrue(result["b_grabbed_x"])
        self.assertEqual(a_reg.drop_calls, 2)
        self.assertEqual(b_reg.drop_calls, 2)


if __name__ == "__main__":
    unittest.main()
