import { useState } from "react";
import type { ResourceMappingResponse } from "../types";

interface Props {
  resourceType: string;
  dataFormat: string | null;
  data: ResourceMappingResponse | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}

function skeletonRows(skeleton: Record<string, unknown>): [string, string][] {
  return Object.entries(skeleton).map(([key, value]) => [key, JSON.stringify(value)]);
}

// Mirrors backend/mapping.py's _INDEX_SUFFIX_RE: a target path like
// "identifier[1]" names a repetition of a top-level field, not a nested
// path -- strip it so hovering a legend row highlights the right skeleton
// key even when the mapping targets a specific repetition.
function skeletonKeyFor(targetPath: string): string {
  return targetPath.replace(/\[\d+\]$/, "");
}

export default function PayloadMappingPanel({ resourceType, dataFormat, data, loading, error, onClose }: Props) {
  const [activePath, setActivePath] = useState<string | null>(null);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal--wide" onClick={(e) => e.stopPropagation()}>
        <div className="payload-mapping-panel__header">
          <h3>{resourceType}: suggested payload</h3>
          <button type="button" className="payload-mapping-panel__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        {loading && <p className="panel__muted">Building suggested payload…</p>}
        {error && <p className="error">{error}</p>}

        {data && (
          <div className="payload-mapping-panel__body">
            <pre className="payload-mapping-panel__skeleton">
              {"{\n"}
              {`  "resourceType": "${resourceType}",\n`}
              {skeletonRows(data.skeleton).map(([key, value]) => (
                <div
                  key={key}
                  className={
                    "payload-mapping-panel__skeleton-line" +
                    (activePath === key ? " payload-mapping-panel__skeleton-line--active" : "")
                  }
                  onMouseEnter={() => setActivePath(key)}
                  onMouseLeave={() => setActivePath((p) => (p === key ? null : p))}
                >
                  {`  "${key}": ${value},`}
                </div>
              ))}
              {"}"}
            </pre>

            <div className="payload-mapping-panel__legend">
              {data.mapped_fields.length === 0 && data.unmapped_fields.length === 0 ? (
                <p className="payload-mapping-panel__hint">
                  {dataFormat === "HL7v2"
                    ? "No verified HL7v2 field mapping is available for this resource yet."
                    : "Paste an HL7v2 sample above to see field-by-field mapping. Other formats show the payload shape only, since there isn't yet a verified mapping source to check a claim against."}
                </p>
              ) : (
                <>
                  {data.mapped_fields.map((field, i) => (
                    <div
                      key={`${field.segment}-${field.field_position}-${i}`}
                      className={
                        "payload-mapping-panel__legend-row" +
                        (activePath === skeletonKeyFor(field.target_path)
                          ? " payload-mapping-panel__legend-row--active"
                          : "")
                      }
                      onMouseEnter={() => setActivePath(skeletonKeyFor(field.target_path))}
                      onMouseLeave={() =>
                        setActivePath((p) => (p === skeletonKeyFor(field.target_path) ? null : p))
                      }
                    >
                      <div className="payload-mapping-panel__legend-row-head">
                        <span className="payload-mapping-panel__legend-tag">
                          {field.segment}-{field.field_position}
                        </span>
                        <span>maps to</span>
                        <span className="payload-mapping-panel__legend-path">
                          {resourceType}.{field.target_path}
                        </span>
                        <a
                          className="resource-card__source"
                          href={field.source_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          source ↗
                        </a>
                      </div>
                      <p className="payload-mapping-panel__legend-detail">
                        {field.field_name}: pasted value "{field.raw_value}"
                        {field.explanation ? ` — ${field.explanation}` : ""}
                        {!field.applied_to_skeleton &&
                          " (shown here, not merged into the payload above -- this field has an internal structure this app doesn't guess at)"}
                      </p>
                    </div>
                  ))}

                  {data.unmapped_fields.length > 0 && (
                    <div className="panel__dropped">
                      <p className="panel__muted">
                        These fields were found in your sample but don't have a verified mapping to{" "}
                        {resourceType} yet:
                      </p>
                      <ul className="dropped-list">
                        {data.unmapped_fields.map((field, i) => (
                          <li key={i}>
                            {field.segment}-{field.field_position}: "{field.raw_value}"
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
