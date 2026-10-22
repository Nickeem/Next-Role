import { useId } from "react";

import type { SectionType, StyleSettings, Templates } from "./types";

const ACCENT_LABEL: Record<string, string> = {
  slate: "Slate",
  navy: "Navy",
  teal: "Teal",
  burgundy: "Burgundy",
  forest: "Forest",
};
const SECTIONS: { type: SectionType; label: string }[] = [
  { type: "summary", label: "Summary" },
  { type: "experience", label: "Experience" },
  { type: "projects", label: "Projects" },
  { type: "skills", label: "Skills" },
  { type: "education", label: "Education" },
  { type: "certifications", label: "Certifications" },
];

/** Template and layout controls. Changing them renders new files; content is unchanged. */
export function StylePanel({
  templates,
  value,
  saved,
  busy,
  onChange,
  onApply,
}: {
  templates: Templates;
  value: StyleSettings;
  saved: StyleSettings;
  busy: boolean;
  onChange: (settings: StyleSettings) => void;
  onApply: () => void;
}) {
  const id = useId();
  const changed = JSON.stringify(value) !== JSON.stringify(saved);
  const set = <K extends keyof StyleSettings>(key: K, v: StyleSettings[K]) => onChange({ ...value, [key]: v });
  return (
    <section className="card" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} className="card__title">
        Design
      </h2>
      <fieldset className="templates">
        <legend className="small">Template</legend>
        {templates.templates.map((t) => (
          <label key={t.id} className={`template-option${value.template_id === t.id ? " template-option--active" : ""}`}>
            <input
              type="radio"
              name={`${id}-template`}
              value={t.id}
              checked={value.template_id === t.id}
              onChange={() => set("template_id", t.id)}
            />
            <span>
              <strong>{t.name}</strong>
              <span className="muted small"> · {t.description}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <div className="style-grid">
        <div className="field">
          <label htmlFor={`${id}-page`}>Page size</label>
          <select id={`${id}-page`} value={value.page_size} onChange={(e) => set("page_size", e.target.value as StyleSettings["page_size"])}>
            <option value="letter">US Letter</option>
            <option value="a4">A4</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${id}-density`}>Density</label>
          <select id={`${id}-density`} value={value.density} onChange={(e) => set("density", e.target.value as StyleSettings["density"])}>
            <option value="normal">Normal</option>
            <option value="compact">Compact</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${id}-accent`}>Accent colour</label>
          <select id={`${id}-accent`} value={value.accent} onChange={(e) => set("accent", e.target.value as StyleSettings["accent"])}>
            {templates.controls.accent.map((a) => (
              <option key={a} value={a}>
                {ACCENT_LABEL[a] ?? a}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${id}-font`}>Font</label>
          <select id={`${id}-font`} value={value.font} onChange={(e) => set("font", e.target.value as StyleSettings["font"])}>
            <option value="sans">Sans serif (Arial)</option>
            <option value="serif">Serif (Times New Roman)</option>
          </select>
        </div>
      </div>
      <fieldset className="toggles">
        <legend className="small">Resume sections shown</legend>
        {SECTIONS.map((s) => (
          <label key={s.type} className="checkbox">
            <input
              type="checkbox"
              checked={!value.hidden_sections.includes(s.type)}
              onChange={(e) =>
                set(
                  "hidden_sections",
                  e.target.checked
                    ? value.hidden_sections.filter((h) => h !== s.type)
                    : [...value.hidden_sections, s.type],
                )
              }
            />
            {s.label}
          </label>
        ))}
      </fieldset>
      <div className="question__actions">
        <button type="button" className="btn btn--primary btn--small" disabled={!changed || busy} onClick={onApply}>
          Apply design
        </button>
        {changed && (
          <button type="button" className="btn btn--secondary btn--small" onClick={() => onChange(saved)}>
            Reset
          </button>
        )}
      </div>
      {changed && <p className="muted small">The preview shows your changes. Applying renders new files.</p>}
    </section>
  );
}
