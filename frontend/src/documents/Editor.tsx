import { useId } from "react";

import { CLAIM_LABEL } from "./labels";
import type { Claim, ClaimCheck, Content, Item, LetterContent, ResumeContent, Section } from "./types";

// A structured editor: the operator changes wording, order and section titles. Employers,
// titles, dates and qualifications come from the profile and are shown read-only.

type Checks = Map<string, ClaimCheck>;

function nextId(prefix: string, existing: Set<string>): string {
  let n = 1;
  while (existing.has(`${prefix}-new-${n}`)) n += 1;
  return `${prefix}-new-${n}`;
}

function allClaimIds(content: Content): Set<string> {
  const ids = new Set<string>();
  const visit = (node: unknown) => {
    if (Array.isArray(node)) node.forEach(visit);
    else if (node && typeof node === "object") {
      const record = node as Record<string, unknown>;
      if (typeof record.claim_id === "string") ids.add(record.claim_id);
      Object.values(record).forEach(visit);
    }
  };
  visit(content);
  return ids;
}

function ClaimList({
  label,
  claims,
  checks,
  saved,
  multiline,
  prefix,
  usedIds,
  onChange,
}: {
  label: string;
  claims: Claim[];
  checks: Checks;
  saved: Map<string, string>;
  multiline: boolean;
  prefix: string;
  usedIds: Set<string>;
  onChange: (claims: Claim[]) => void;
}) {
  const baseId = useId();
  const update = (index: number, patch: Partial<Claim>) =>
    onChange(claims.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  const move = (index: number, by: number) => {
    const next = [...claims];
    const target = index + by;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target] as Claim, next[index] as Claim];
    onChange(next);
  };
  return (
    <div className="claim-list" role="group" aria-label={label}>
      {claims.map((claim, i) => {
        const check = checks.get(claim.claim_id);
        const unchanged = saved.get(claim.claim_id) === claim.text;
        const fieldId = `${baseId}-${i}`;
        return (
          <div key={claim.claim_id} className="claim-row">
            <label htmlFor={fieldId} className="sr-only">
              {label} {i + 1}
              {claim.connective ? " (connecting sentence)" : ""}
            </label>
            {multiline ? (
              <textarea
                id={fieldId}
                rows={2}
                maxLength={600}
                value={claim.text}
                onChange={(e) => update(i, { text: e.target.value })}
              />
            ) : (
              <input
                id={fieldId}
                maxLength={600}
                value={claim.text}
                onChange={(e) => update(i, { text: e.target.value })}
              />
            )}
            <div className="claim-row__meta">
              {claim.connective && <span className="muted small">Connecting sentence</span>}
              {check && unchanged ? (
                <span className={`claim claim--${check.status}`}>{CLAIM_LABEL[check.status]}</span>
              ) : (
                <span className="claim claim--pending">{check ? "Edited: checked on save" : "New: checked on save"}</span>
              )}
              <button
                type="button"
                className="btn btn--secondary btn--small"
                onClick={() => move(i, -1)}
                disabled={i === 0}
                aria-label={`Move ${label.toLowerCase()} ${i + 1} up`}
              >
                ↑
              </button>
              <button
                type="button"
                className="btn btn--secondary btn--small"
                onClick={() => move(i, 1)}
                disabled={i === claims.length - 1}
                aria-label={`Move ${label.toLowerCase()} ${i + 1} down`}
              >
                ↓
              </button>
              <button
                type="button"
                className="btn btn--danger-outline btn--small"
                onClick={() => onChange(claims.filter((_, j) => j !== i))}
                aria-label={`Remove ${label.toLowerCase()} ${i + 1}`}
              >
                Remove
              </button>
            </div>
          </div>
        );
      })}
      <button
        type="button"
        className="btn btn--secondary btn--small"
        onClick={() => {
          const claim_id = nextId(prefix, usedIds);
          usedIds.add(claim_id);
          onChange([...claims, { claim_id, text: "", evidence_ids: [], connective: false }]);
        }}
      >
        Add {label.split(" ")[0]?.toLowerCase()}
        <span className="sr-only"> ({label})</span>
      </button>
    </div>
  );
}

function itemHeading(item: Item): string {
  const lead = item.position ?? item.name ?? item.qualification ?? "";
  const where = item.organization ?? item.institution ?? item.issuer ?? "";
  return [lead, where].filter((x) => typeof x === "string" && x).join(", ");
}

function ResumeEditor({
  content,
  checks,
  saved,
  onChange,
  onRedraft,
  busy,
}: {
  content: ResumeContent;
  checks: Checks;
  saved: Map<string, string>;
  onChange: (content: ResumeContent) => void;
  onRedraft: (section: string) => void;
  busy: boolean;
}) {
  const usedIds = allClaimIds(content);
  const setSection = (index: number, section: Section) =>
    onChange({ ...content, sections: content.sections.map((s, i) => (i === index ? section : s)) });
  return (
    <div className="editor">
      {content.sections.map((section, s) => (
        <fieldset key={section.id} className="editor__section">
          <legend>
            <input
              className="editor__title"
              aria-label={`Title of the ${section.type} section`}
              value={section.title}
              maxLength={300}
              onChange={(e) => setSection(s, { ...section, title: e.target.value })}
            />
            <button
              type="button"
              className="btn btn--secondary btn--small"
              disabled={busy}
              onClick={() => onRedraft(section.type)}
            >
              Redraft section
            </button>
          </legend>
          {section.claims && (
            <ClaimList
              label="Summary sentence"
              claims={section.claims}
              checks={checks}
              saved={saved}
              multiline
              prefix="sum"
              usedIds={usedIds}
              onChange={(claims) => setSection(s, { ...section, claims })}
            />
          )}
          {section.skills && (
            <ClaimList
              label="Skill"
              claims={section.skills}
              checks={checks}
              saved={saved}
              multiline={false}
              prefix="skill"
              usedIds={usedIds}
              onChange={(skills) => setSection(s, { ...section, skills })}
            />
          )}
          {section.items?.map((item, i) => (
            <div key={item.id} className="editor__item">
              <p className="editor__item-head">
                <strong>{itemHeading(item)}</strong>{" "}
                <span className="muted small">(from your profile)</span>
              </p>
              {item.bullets && (
                <ClaimList
                  label={`Bullet for ${itemHeading(item)}`}
                  claims={item.bullets}
                  checks={checks}
                  saved={saved}
                  multiline
                  prefix={item.id}
                  usedIds={usedIds}
                  onChange={(bullets) =>
                    setSection(s, {
                      ...section,
                      items: section.items?.map((it, j) => (j === i ? { ...it, bullets } : it)),
                    })
                  }
                />
              )}
            </div>
          ))}
        </fieldset>
      ))}
    </div>
  );
}

function LetterEditor({
  content,
  checks,
  saved,
  onChange,
  onRedraft,
  busy,
}: {
  content: LetterContent;
  checks: Checks;
  saved: Map<string, string>;
  onChange: (content: LetterContent) => void;
  onRedraft: () => void;
  busy: boolean;
}) {
  const usedIds = allClaimIds(content);
  return (
    <div className="editor">
      <p>
        <button type="button" className="btn btn--secondary btn--small" disabled={busy} onClick={onRedraft}>
          Redraft the letter
        </button>
      </p>
      {content.paragraphs.map((paragraph, p) => (
        <fieldset key={paragraph.id} className="editor__section">
          <legend>Paragraph {p + 1}</legend>
          <ClaimList
            label={`Sentence in paragraph ${p + 1}`}
            claims={paragraph.claims}
            checks={checks}
            saved={saved}
            multiline
            prefix={paragraph.id}
            usedIds={usedIds}
            onChange={(claims) =>
              onChange({
                ...content,
                paragraphs: content.paragraphs.map((x, i) => (i === p ? { ...x, claims } : x)),
              })
            }
          />
        </fieldset>
      ))}
    </div>
  );
}

export function Editor({
  content,
  checks,
  saved,
  onChange,
  onRedraft,
  busy,
}: {
  content: Content;
  checks: ClaimCheck[];
  saved: Map<string, string>;
  onChange: (content: Content) => void;
  onRedraft: (section: string) => void;
  busy: boolean;
}) {
  const byId = new Map(checks.map((c) => [c.claim_id, c]));
  return content.kind === "resume" ? (
    <ResumeEditor
      content={content}
      checks={byId}
      saved={saved}
      onChange={onChange}
      onRedraft={onRedraft}
      busy={busy}
    />
  ) : (
    <LetterEditor
      content={content}
      checks={byId}
      saved={saved}
      onChange={onChange}
      onRedraft={() => onRedraft("letter")}
      busy={busy}
    />
  );
}

/** Empty claims are dropped before saving; empty sections and paragraphs too. */
export function cleaned(content: Content): Content {
  const keep = (claims: Claim[] | undefined) =>
    claims?.map((c) => ({ ...c, text: c.text.trim() })).filter((c) => c.text) ;
  if (content.kind === "cover_letter") {
    return {
      ...content,
      paragraphs: content.paragraphs
        .map((p) => ({ ...p, claims: keep(p.claims) ?? [] }))
        .filter((p) => p.claims.length > 0),
    };
  }
  return {
    ...content,
    sections: content.sections
      .map((s) => ({
        ...s,
        ...(s.claims ? { claims: keep(s.claims) } : {}),
        ...(s.skills ? { skills: keep(s.skills) } : {}),
        ...(s.items ? { items: s.items.map((it) => (it.bullets ? { ...it, bullets: keep(it.bullets) } : it)) } : {}),
      }))
      .filter((s) => (s.claims ? s.claims.length > 0 : true) && (s.skills ? s.skills.length > 0 : true)),
  };
}
