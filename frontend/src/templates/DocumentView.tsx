import type { CSSProperties } from "react";

import type { DesignTokens, DisplayDocument, DisplaySection, DisplayText } from "./types";

// One component renders all three templates; they differ only in design tokens and the
// heading style class. Everything is text (React escapes it); nothing is fetched.

type Mode = "print" | "preview";

function cssVars(t: DesignTokens): CSSProperties {
  return {
    "--nr-font": t.font_css,
    "--nr-body": `${t.body_pt}pt`,
    "--nr-small": `${t.small_pt}pt`,
    "--nr-name": `${t.name_pt}pt`,
    "--nr-heading": `${t.heading_pt}pt`,
    "--nr-line": String(t.line_height),
    "--nr-section-gap": `${t.section_gap_pt}pt`,
    "--nr-item-gap": `${t.item_gap_pt}pt`,
    "--nr-accent": t.accent,
    "--nr-text": t.text,
    "--nr-muted": t.muted,
    "--nr-margin": `${t.margin_in}in`,
    "--nr-page-width": `${t.page.width_in}in`,
    "--nr-page-height": `${t.page.height_in}in`,
  } as CSSProperties;
}

function Joined({ items, separator }: { items: DisplayText[]; separator: string }) {
  return (
    <>
      {items.map((item, i) => (
        <span key={item.claim_id ?? i} data-claim={item.claim_id ?? undefined}>
          {i > 0 ? separator : ""}
          {item.text}
        </span>
      ))}
    </>
  );
}

function Section({ section }: { section: DisplaySection }) {
  return (
    <section className={`nr-section nr-section-${section.type}`} aria-label={section.title}>
      <h2 className="nr-title">{section.title}</h2>
      {section.inline.length > 0 && (
        <p className={section.type === "skills" ? "nr-skills" : "nr-summary"}>
          <Joined items={section.inline} separator={section.type === "skills" ? " · " : " "} />
        </p>
      )}
      {section.entries.map((entry, i) => (
        <div className="nr-entry" key={i}>
          <div className="nr-entry-head">
            <span className="nr-entry-title">
              <strong>{entry.heading}</strong>
              {entry.subheading ? `, ${entry.subheading}` : ""}
            </span>
            {entry.dates && <span className="nr-dates">{entry.dates}</span>}
          </div>
          {entry.location && <div className="nr-location">{entry.location}</div>}
          {entry.details && entry.details.length > 0 && (
            <div className="nr-details">{entry.details.join("  ·  ")}</div>
          )}
          {entry.bullets.length > 0 && (
            <ul className="nr-bullets">
              {entry.bullets.map((b, j) => (
                <li key={b.claim_id ?? j} data-claim={b.claim_id ?? undefined}>
                  {b.text}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </section>
  );
}

export function DocumentView({
  document: doc,
  tokens,
  mode = "preview",
}: {
  document: DisplayDocument;
  tokens: DesignTokens;
  mode?: Mode;
}) {
  const classes = [
    "nr-doc",
    `nr-${tokens.template_id}`,
    `nr-h-${tokens.heading_style}`,
    tokens.accent_name ? "nr-accent-name" : "",
    `nr-${mode}`,
    `nr-kind-${doc.kind}`,
  ];
  return (
    <article className={classes.join(" ")} style={cssVars(tokens)}>
      <header className="nr-head">
        <h1 className="nr-name">{doc.header.name}</h1>
        {doc.header.headline && <p className="nr-headline">{doc.header.headline}</p>}
        {doc.header.contact.length > 0 && (
          <p className="nr-contact">
            {doc.header.contact.map((c, i) => (
              <span key={i}>
                {i > 0 ? " | " : ""}
                {c}
              </span>
            ))}
          </p>
        )}
      </header>
      {doc.kind === "resume" ? (
        doc.sections.map((s) => <Section key={s.id} section={s} />)
      ) : (
        <div className="nr-letter">
          {doc.regarding && <p className="nr-regarding">{doc.regarding}</p>}
          {doc.salutation && <p>{doc.salutation}</p>}
          {doc.paragraphs.map((p, i) => (
            <p key={i}>
              <Joined items={p} separator=" " />
            </p>
          ))}
          {doc.closing && <p className="nr-closing">{doc.closing}</p>}
          {doc.closing && <p className="nr-signature">{doc.header.name}</p>}
        </div>
      )}
    </article>
  );
}
