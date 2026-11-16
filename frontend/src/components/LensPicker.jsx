/** Dropdown of library lenses + "custom" that reveals a textarea. value = {name, text}. */
export default function LensPicker({ lenses, value, onChange, compact = false }) {
  const name = value?.name || "";
  const text = value?.text || "";
  const isLibrary = lenses.some((l) => l.name === name && l.text === text);
  const mode = !text ? "none" : isLibrary ? "library" : "custom";
  const cats = [...new Set(lenses.map((l) => l.category))];

  const pick = (e) => {
    const v = e.target.value;
    if (v === "") return onChange({ name: "", text: "" });
    if (v === "__custom") return onChange({ name: name || "Custom", text: text || " " });
    const l = lenses.find((x) => x.name === v);
    if (l) onChange({ name: l.name, text: l.text });
  };

  return (
    <span className={`lens-picker ${compact ? "compact" : ""}`}>
      <select value={mode === "custom" ? "__custom" : name} onChange={pick} title={text}>
        <option value="">no lens</option>
        {cats.map((c) => <optgroup key={c} label={c}>{lenses.filter((l) => l.category === c).map((l) => <option key={l.id} value={l.name}>{l.name}</option>)}</optgroup>)}
        <option value="__custom">custom…</option>
      </select>
      {mode === "custom" && !compact && (
        <span className="lens-custom">
          <input placeholder="lens name" value={name} onChange={(e) => onChange({ name: e.target.value, text })} />
          <textarea rows={3} placeholder="What this seat prioritizes, is suspicious of, won't let slide." value={text.trim() === "" ? "" : text} onChange={(e) => onChange({ name, text: e.target.value })} />
        </span>
      )}
    </span>
  );
}
