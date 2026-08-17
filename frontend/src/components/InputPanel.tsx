import { useMemo, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import type { StartFields } from "../types";

const DATA_FORMATS = ["CSV", "HL7v2", "JSON", "XML", "Other"];
const TERMINOLOGY_SYSTEMS = ["LOINC", "SNOMED CT", "ICD-10-CM", "RxNorm", "CPT", "UCUM", "Other"];
const DEFAULT_MODEL = "claude-sonnet-5";
const DATA_SAMPLE_MAX_CHARS = 4000;

interface Props {
  models: string[];
  modelsLoading: boolean;
  submitting: boolean;
  onStart: (fields: StartFields) => void;
}

function groupModels(models: string[]): Map<string, string[]> {
  const groups = new Map<string, string[]>();
  for (const model of models) {
    const prefix = model.split("-")[0];
    const list = groups.get(prefix) ?? [];
    list.push(model);
    groups.set(prefix, list);
  }
  return groups;
}

export default function InputPanel({ models, modelsLoading, submitting, onStart }: Props) {
  const [message, setMessage] = useState("");
  const [dataSample, setDataSample] = useState("");
  const [dataFormat, setDataFormat] = useState("");
  const [terminologySystem, setTerminologySystem] = useState("");
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [truncated, setTruncated] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const modelGroups = useMemo(() => groupModels(models), [models]);

  function applySample(text: string) {
    const wasTruncated = text.length > DATA_SAMPLE_MAX_CHARS;
    setDataSample(text.slice(0, DATA_SAMPLE_MAX_CHARS));
    setTruncated(wasTruncated);
  }

  function handleFileChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => applySample(String(reader.result ?? ""));
    reader.readAsText(file);
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const text = message.trim();
    if (!text || submitting) return;

    onStart({
      message: text,
      data_sample: dataSample.trim() || null,
      data_format: dataFormat || null,
      terminology_system: terminologySystem || null,
      model,
    });
  }

  return (
    <form className="input-panel" onSubmit={handleSubmit}>
      <label className="field">
        <span className="field__label">Use case</span>
        <textarea
          className="field__textarea"
          rows={2}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder={'e.g. "raw data from vital monitors" or "supply orders and charges"'}
          disabled={submitting}
        />
      </label>

      <div className="field-row">
        <label className="field">
          <span className="field__label">Source data format</span>
          <select
            className="field__select"
            value={dataFormat}
            onChange={(e) => setDataFormat(e.target.value)}
            disabled={submitting}
          >
            <option value="">None specified</option>
            {DATA_FORMATS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="field__label">Desired terminology system</span>
          <select
            className="field__select"
            value={terminologySystem}
            onChange={(e) => setTerminologySystem(e.target.value)}
            disabled={submitting}
          >
            <option value="">None specified</option>
            {TERMINOLOGY_SYSTEMS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="field__label">Model</span>
          <select
            className="field__select"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={submitting || modelsLoading}
          >
            {modelsLoading && <option>Loading…</option>}
            {[...modelGroups.entries()].map(([prefix, ids]) => (
              <optgroup key={prefix} label={prefix}>
                {ids.map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
      </div>

      <label className="field">
        <span className="field__label">
          Sample data <span className="field__hint">(optional — used as context, not parsed)</span>
        </span>
        <textarea
          className="field__textarea field__textarea--mono"
          rows={4}
          value={dataSample}
          onChange={(e) => applySample(e.target.value)}
          placeholder="Paste a sample row, message, or payload…"
          disabled={submitting}
        />
        <div className="field__file-row">
          <button
            type="button"
            className="field__file-button"
            onClick={() => fileInputRef.current?.click()}
            disabled={submitting}
          >
            Upload file
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.txt,.json,.xml,.hl7"
            className="field__file-input"
            onChange={handleFileChange}
            disabled={submitting}
          />
          {truncated && (
            <span className="field__hint field__hint--warn">
              Truncated to {DATA_SAMPLE_MAX_CHARS.toLocaleString()} characters
            </span>
          )}
        </div>
      </label>

      <button type="submit" className="input-panel__submit" disabled={submitting || !message.trim()}>
        {submitting ? "Thinking…" : "Start"}
      </button>
    </form>
  );
}
