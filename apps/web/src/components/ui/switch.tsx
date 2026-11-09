// Adapted from shadcn/ui (MIT, https://ui.shadcn.com), restyled for SafeGuard.
import * as SwitchPrimitive from "@radix-ui/react-switch";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export function Switch({ className, ...props }: ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      className={cn(
        "peer inline-flex h-6 w-10 shrink-0 cursor-pointer items-center rounded-full border border-rule-strong transition-colors",
        "data-[state=checked]:border-binding data-[state=checked]:bg-binding data-[state=unchecked]:bg-sunken",
        "disabled:cursor-not-allowed disabled:opacity-60",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        className={cn(
          "pointer-events-none block size-[1.125rem] rounded-full shadow-sm ring-0 transition-transform",
          "data-[state=checked]:translate-x-[1.05rem] data-[state=checked]:bg-on-binding",
          "data-[state=unchecked]:translate-x-[0.15rem] data-[state=unchecked]:bg-ink-muted",
        )}
      />
    </SwitchPrimitive.Root>
  );
}
