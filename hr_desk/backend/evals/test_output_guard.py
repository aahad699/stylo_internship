"""The output guard restates hedges about the evidence and refuses only real leaks."""

import unittest

from app.config import CANARY
from app.replies import REFUSAL, UNKNOWN
from app.sanitize import fixed_reply, forbidden_output, sanitize

CLAIMS = "In-patient claims at a non-panel hospital are submitted to HR with the In-Patient Claim Form."
RATES = "Regional Sales Manager: Daily Tour Allowance Rs. 1,500. Area Sales Manager: Rs. 1,000."


class OutputGuardTests(unittest.TestCase):
    def test_annual_leave_number_requires_annual_leave_evidence(self):
        answer = "You get 18 days of annual leave per year."
        self.assertEqual(sanitize(answer, ["Children are covered until age 18."]), UNKNOWN)
        self.assertEqual(sanitize(answer, ["Employees receive 18 days of annual leave per year."]), answer)

    def test_notice_referral_is_unknown_without_discarding_a_partial_answer(self):
        draft = ("Notice period for resignation: You need to refer to your employment terms. "
                 "The policy does not specify a general notice period for resignations. "
                 "Please confirm your current contract or local statutory requirements, and I can outline the exact notice period and any exceptions.")
        self.assertEqual(fixed_reply(draft), UNKNOWN)
        self.assertEqual(fixed_reply("The policy does not specify a notice period, but requires written resignation."), "")

    def test_labeled_unknown_is_canonical_but_mixed_answer_is_not(self):
        for draft in ("Topic: I couldn't find this in the policies I can open. Ask HR.",
                      "Travel policy: I couldn’t find this in the policies I can open."):
            with self.subTest(draft=draft):
                self.assertEqual(fixed_reply(draft), UNKNOWN)
                self.assertEqual(sanitize(draft, []), UNKNOWN)
        self.assertEqual(fixed_reply("The rate is 100.\n\nTopic: I couldn't find this. Ask HR."), "")

    def test_labeled_missing_percentage_is_unknown_but_not_a_partial_answer(self):
        draft = "PF deduction: I couldn't find this exact percentage in the policy. The policy covers loans but not the monthly contribution rate."
        self.assertEqual(fixed_reply(draft), UNKNOWN)
        mixed = "PF deduction: I couldn't find this exact percentage, but another limit is 75%."
        self.assertEqual(fixed_reply(mixed), "")
        self.assertEqual(fixed_reply("Provident Fund deduction percentage: I couldn't find this specific percentage in the policy. The PF policy covers loan deductions and repayment, but not the monthly contribution rate."), UNKNOWN)

    def test_equivalent_evidence_hedges_are_restated_as_the_policy(self):
        cases = {
            "The policy text provided does not specify a time limit for non-panel claims.":
                "The policy does not specify a time limit for non-panel claims.",
            "The policy excerpt you provided does not specify a time limit for non-panel claims.":
                "The policy does not specify a time limit for non-panel claims.",
            "The policy evidence provided does not specify a time limit for non-panel claims.":
                "The policy does not specify a time limit for non-panel claims.",
            "The exact preparer isn't specified in your excerpt.":
                "The exact preparer isn't specified in the policy.",
            "Assistant General Manager isn't shown in the rates you provided.":
                "Assistant General Manager isn't shown in the policy.",
            "The rate for Assistant General Manager isn't specified in the policy text you gave me.":
                "The rate for Assistant General Manager isn't specified in the policy.",
            "Assistant General Manager isn't listed in the provided rates.":
                "Assistant General Manager isn't listed in the policy.",
            "The passages say an advocate is not allowed at the inquiry.":
                "The policy says an advocate is not allowed at the inquiry.",
            "The supplied policy says an advocate is not allowed.":
                "The policy says an advocate is not allowed.",
            "Nothing in what you provided covers an advocate at the inquiry.":
                "Nothing in the policy covers an advocate at the inquiry.",
        }
        for draft, expected in cases.items():
            with self.subTest(draft=draft):
                self.assertFalse(forbidden_output(draft))
                self.assertEqual(sanitize(draft, [CLAIMS]), expected)

    def test_ordinary_wording_about_the_employee_is_left_alone(self):
        for answer in ("HR reimburses the claim provided documents are attached.",
                       "Based on the details you provided, you qualify if the trip is over 300 km."):
            with self.subTest(answer=answer):
                self.assertEqual(sanitize(answer, ["300 km"]), answer)

    def test_hedge_keeps_the_grounded_answer(self):
        draft = "Daily Tour Allowance: Based on the policy data you provided, a Regional Sales Manager gets Rs. 1,500."
        self.assertEqual(sanitize(draft, [RATES]),
                         "Daily Tour Allowance: Based on the policy, a Regional Sales Manager gets Rs. 1,500.")

    def test_request_for_policy_text_is_dropped_and_the_answer_kept(self):
        draft = ("Submit the In-Patient Claim Form to HR. "
                 "Please provide the policy text so I can confirm the deadline.")
        self.assertEqual(sanitize(draft, [CLAIMS]), "Submit the In-Patient Claim Form to HR.")

    def test_real_leaks_are_still_refused(self):
        for draft in (f"Sure. {CANARY}", "The internal marker says otherwise.", "See {{designation}}.",
                      "Check C:\\HR\\rates.pdf for the rate.", "See /srv/hr/policy.docx."):
            with self.subTest(draft=draft):
                self.assertTrue(forbidden_output(draft))
                self.assertEqual(sanitize(draft, [CLAIMS]), REFUSAL)


if __name__ == "__main__":
    unittest.main()
