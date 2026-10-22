import type {
  ArtifactStatus,
  Bundle,
  BundleStatus,
  ClaimCheck,
  ClaimStatus,
  Content,
  DocumentKind,
  RunStatus,
} from "./types";

export const BUNDLE_LABEL: Record<BundleStatus, string> = {
  needs_input: "Needs your input",
  ready_for_review: "Ready for approval",
  approved: "Approved",
  withdrawn: "Withdrawn",
};

export const KIND_LABEL: Record<DocumentKind, string> = {
  resume: "Resume",
  cover_letter: "Cover letter",
};

export const CLAIM_LABEL: Record<ClaimStatus, string> = {
  supported: "Supported",
  needs_review: "Needs review",
  unsupported: "Not supported",
};

export const ARTIFACT_LABEL: Record<ArtifactStatus, string> = {
  pending: "Waiting to render",
  rendering: "Rendering",
  ready: "Ready",
  failed: "Failed",
  superseded: "Replaced",
};

export const RUN_LABEL: Record<string, string> = {
  initial: "Drafting documents",
  regenerate_section: "Redrafting a section",
  verify: "Checking claims",
};

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  queued: "queued",
  running: "in progress",
  completed: "done",
  failed: "failed",
  cancelled: "cancelled",
};

/** Plain-language explanation of a run failure code (codes never contain personal data). */
export function runError(code: string | null): string {
  switch (code) {
    case "stale_authorization":
      return "The approval to draft no longer matches this role (it was revoked or the role was re-assessed). Approve it again from the review.";
    case "revision_changed":
      return "The document changed while it was being redrafted; it will be redrafted again.";
    case "approval_revoked":
      return "The approval to draft was revoked.";
    default:
      return code ? `Error: ${code}. It will be retried automatically if possible.` : "";
  }
}

/** Claim id -> text in the content, so checks can quote what they are about. */
export function claimTexts(content: Content): Map<string, string> {
  const texts = new Map<string, string>();
  if (content.kind === "cover_letter") {
    for (const p of content.paragraphs) for (const c of p.claims) texts.set(c.claim_id, c.text);
    return texts;
  }
  for (const section of content.sections) {
    for (const c of [...(section.claims ?? []), ...(section.skills ?? [])]) {
      texts.set(c.claim_id, c.text);
    }
    for (const item of section.items ?? []) {
      const heading = [item.position ?? item.name ?? item.qualification, item.organization ?? item.institution ?? item.issuer]
        .filter((x) => typeof x === "string" && x)
        .join(", ");
      texts.set(`item-${item.id}`, heading);
      for (const c of item.bullets ?? []) texts.set(c.claim_id, c.text);
    }
  }
  return texts;
}

export function problems(checks: ClaimCheck[] | undefined): ClaimCheck[] {
  return (checks ?? []).filter((c) => c.status !== "supported");
}

export interface Blocker {
  text: string;
}

/** Why approval is not possible yet, in the order the operator should resolve them. */
export function approvalBlockers(bundle: Bundle): Blocker[] {
  const blockers: Blocker[] = [];
  if (bundle.status === "withdrawn") return [{ text: "The approval to draft was revoked." }];
  if (bundle.status === "approved") return [];
  if (bundle.runs.some((r) => r.status === "queued" || r.status === "running")) {
    blockers.push({ text: "Wait for drafting and checks to finish." });
  }
  for (const kind of ["resume", "cover_letter"] as const) {
    const revision = bundle[kind];
    if (!revision) {
      blockers.push({ text: `The ${KIND_LABEL[kind].toLowerCase()} has not been drafted.` });
    } else if (!revision.verification) {
      blockers.push({ text: `The ${KIND_LABEL[kind].toLowerCase()} is waiting to be checked.` });
    } else if (revision.verification.status !== "passed") {
      const count = problems(revision.verification.claims).length;
      blockers.push({
        text: `${count} item${count === 1 ? "" : "s"} in the ${KIND_LABEL[kind].toLowerCase()} need${count === 1 ? "s" : ""} attention.`,
      });
    }
  }
  const required = bundle.clarifications.filter((q) => q.material && q.status === "open").length;
  if (required) {
    blockers.push({
      text: `${required} required question${required === 1 ? "" : "s"} to answer or dismiss.`,
    });
  }
  const files = bundle.artifacts;
  if (files.length !== 4 || files.some((a) => a.status !== "ready" || !a.qa?.passed)) {
    blockers.push({ text: "All four files (PDF and DOCX of both documents) must be ready." });
  }
  return blockers;
}

export function isBusy(bundle: Bundle): boolean {
  return (
    bundle.runs.some((r) => r.status === "queued" || r.status === "running") ||
    bundle.artifacts.some((a) => a.status === "pending" || a.status === "rendering")
  );
}

export function newKey(): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return random.replace(/[^A-Za-z0-9_-]/g, "");
}
