import { overridesAssessment, requiresConfirmation, requiresReason } from "./labels";

// Must match tests/unit/test_review_workflow.py::test_overrides_assessment.
test.each([
  ["approve_for_drafting", "weak", false, true],
  ["approve_for_drafting", "insufficient_data", false, true],
  ["approve_for_drafting", "strong", true, true],
  ["approve_for_drafting", "moderate", false, false],
  ["shortlist", "weak", false, true],
  ["reject", "strong", false, true],
  ["reject", "strong", true, false],
  ["reject", "weak", false, false],
  ["postpone", "weak", true, false],
] as const)("%s on %s (gaps=%s) overrides: %s", (action, tier, gaps, expected) => {
  expect(overridesAssessment(action, tier, gaps)).toBe(expected);
});

test("reopen always needs a reason; approve and reject need confirmation", () => {
  expect(requiresReason("reopen", "strong", false)).toBe(true);
  expect(requiresConfirmation("approve_for_drafting")).toBe(true);
  expect(requiresConfirmation("reject")).toBe(true);
  expect(requiresConfirmation("shortlist")).toBe(false);
});
