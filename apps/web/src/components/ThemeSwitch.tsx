import { useEffect, useState } from "react";
import { type ThemeChoice, applyTheme, storeTheme, storedTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { Monitor, Moon, Sun } from "./icons";

const CHOICES: { value: ThemeChoice; label: string; Icon: typeof Sun }[] = [
  { value: "system", label: "Match system", Icon: Monitor },
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
];

export function ThemeSwitch() {
  const [choice, setChoice] = useState<ThemeChoice>(storedTheme);

  useEffect(() => {
    applyTheme(choice);
    storeTheme(choice);
    if (choice !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const follow = () => {
      applyTheme("system");
    };
    media.addEventListener("change", follow);
    return () => {
      media.removeEventListener("change", follow);
    };
  }, [choice]);

  return (
    <fieldset className="flex rounded-md border border-rule bg-surface p-0.5">
      <legend className="sr-only">Theme</legend>
      {CHOICES.map(({ value, label, Icon }) => (
        <label
          key={value}
          title={label}
          className={cn(
            "relative flex size-8 cursor-pointer items-center justify-center rounded text-ink-muted hover:text-ink",
            "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-1 has-[:focus-visible]:outline-focus",
            choice === value && "bg-binding-wash text-ink",
          )}
        >
          <input
            type="radio"
            name="theme"
            value={value}
            checked={choice === value}
            onChange={() => {
              setChoice(value);
            }}
            className="sr-only"
            aria-label={label}
          />
          <Icon className="size-4" />
        </label>
      ))}
    </fieldset>
  );
}
