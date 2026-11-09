import { Switch } from "@/components/ui/switch";
import { familyOf, labelOf } from "@/lib/entities";
import type { Group } from "@/lib/segments";

function confidence(group: Group): string {
  if (group.locked) return "Placeholder-shaped text in your input, always wrapped";
  if (group.score === null) return "Another occurrence of a value found above";
  return `Confidence ${Math.round(group.score * 100)}%`;
}

function GroupRow({ group, onToggle }: { group: Group; onToggle: (placeholder: string) => void }) {
  const id = `mask-${group.placeholder.replace(/[^A-Za-z0-9]/g, "")}`;
  const family = familyOf(group.type);
  return (
    <li className={`fam-${family} flex items-start gap-3 border-b border-rule py-3 last:border-b-0`}>
      <Switch
        id={id}
        checked={group.masked}
        disabled={group.locked}
        onCheckedChange={() => {
          onToggle(group.placeholder);
        }}
        className="mt-0.5"
        aria-describedby={`${id}-meta`}
      />
      <div className="min-w-0 flex-1">
        <label htmlFor={id} className="block cursor-pointer">
          <span className="block text-[0.8125rem] font-semibold text-(--fam)">
            {labelOf(group.type)}
          </span>
          <span className={`block break-words ${group.masked ? "" : "text-ink"}`}>{group.value}</span>
        </label>
        <p id={`${id}-meta`} className="mt-1 text-[0.8125rem] text-ink-muted">
          <span className="token text-ink">{group.placeholder}</span>
          {group.count > 1 ? `, ${group.count} places` : ""}. {group.masked ? "" : "Switched off: goes into your request as written. "}
          {confidence(group)}
        </p>
        {group.score !== null && (
          <div className="mt-1.5 h-1 w-full max-w-40 rounded-full bg-sunken" aria-hidden="true">
            <div className="h-1 rounded-full bg-(--fam)" style={{ width: `${Math.round(group.score * 100)}%` }} />
          </div>
        )}
      </div>
    </li>
  );
}

export function Ledger({ groups, onToggle }: { groups: Group[]; onToggle: (placeholder: string) => void }) {
  const places = groups.reduce((sum, g) => sum + g.count, 0);
  const off = groups.filter((g) => !g.masked).length;
  return (
    <section aria-labelledby="ledger-title" className="flex flex-col">
      <h3 id="ledger-title" className="font-display text-xl">
        Found on this computer
      </h3>
      <p className="mt-1 text-sm text-ink-muted" aria-live="polite">
        {groups.length === 0
          ? "No personal data was detected. Detection reduces exposure; it can miss things, so read the text before you send it."
          : `${groups.length} ${groups.length === 1 ? "value" : "values"} in ${places} ${places === 1 ? "place" : "places"}` +
            (off > 0 ? `, ${off} switched off.` : ", all masked.")}
      </p>
      {groups.length > 0 && (
        <ul className="mt-2" aria-label="Detected values">
          {groups.map((group) => (
            <GroupRow key={group.placeholder} group={group} onToggle={onToggle} />
          ))}
        </ul>
      )}
    </section>
  );
}
