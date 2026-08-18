import unittest

from src.services.course_service import CourseService
from src.tui.app import _registration_window_text


class FakeClient:
    async def get_semesters_with_periods(self):
        return [
            {
                "id": 14,
                "semesterName": "2_2025_2026",
                "semesterRegisterPeriods": [
                    {"id": 66, "name": "Học kỳ chính"},
                    {"id": 72, "name": "Học kỳ phụ"},
                ],
            },
            {
                "id": 15,
                "semesterName": "1_2026_2027",
                "semesterRegisterPeriods": [
                    {"id": 78, "name": "Học kỳ hè"},
                ],
            },
        ]


class CourseServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_period_picker_flattens_every_semester_and_period(self):
        periods = await CourseService(FakeClient()).get_registration_periods()

        self.assertEqual([period["id"] for period in periods], [66, 72, 78])
        self.assertEqual(periods[2]["semester"]["semesterName"], "1_2026_2027")


class RegistrationWindowTextTest(unittest.TestCase):
    def test_formats_window_in_vietnam_time(self):
        text = _registration_window_text({
            "startDate": 1_735_689_600_000,
            "endDate": 1_735_693_200_000,
        })

        self.assertEqual(
            text,
            "Thời gian đăng ký: 07:00 01/01/2025 → 08:00 01/01/2025",
        )


if __name__ == "__main__":
    unittest.main()
