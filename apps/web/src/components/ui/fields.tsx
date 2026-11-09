// Adapted from shadcn/ui (MIT, https://ui.shadcn.com): Input, Textarea and Label.
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

const FIELD =
  "w-full rounded-md border border-rule-strong bg-surface text-ink placeholder:text-ink-muted disabled:opacity-60";

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn(FIELD, "h-10 px-3 text-[0.9375rem]", className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea className={cn(FIELD, "px-4 py-3 text-base leading-relaxed", className)} {...props} />;
}

export function Label({ className, htmlFor, ...props }: ComponentProps<"label">) {
  return <label htmlFor={htmlFor} className={cn("text-sm font-medium text-ink", className)} {...props} />;
}
