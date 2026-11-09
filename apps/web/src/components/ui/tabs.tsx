// Adapted from shadcn/ui (MIT, https://ui.shadcn.com): folder tabs on a sheet.
import * as TabsPrimitive from "@radix-ui/react-tabs";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export const Tabs = TabsPrimitive.Root;

export function TabsList({ className, ...props }: ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      className={cn("relative z-10 -mb-px flex items-end gap-1 overflow-x-auto px-1", className)}
      {...props}
    />
  );
}

export function TabsTrigger({ className, ...props }: ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "shrink-0 rounded-t-lg border border-transparent px-4 py-2.5 text-[0.9375rem] text-ink-muted transition-colors hover:text-ink",
        "data-[state=active]:border-rule data-[state=active]:border-b-surface data-[state=active]:bg-surface data-[state=active]:font-semibold data-[state=active]:text-ink",
        className,
      )}
      {...props}
    />
  );
}

export function TabsContent({ className, ...props }: ComponentProps<typeof TabsPrimitive.Content>) {
  return (
    <TabsPrimitive.Content
      className={cn("rounded-b-xl rounded-tr-xl border border-rule bg-surface", className)}
      {...props}
    />
  );
}
