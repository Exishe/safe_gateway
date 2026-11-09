// Adapted from shadcn/ui (MIT, https://ui.shadcn.com), restyled for SafeGuard.
import { type VariantProps, cva } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

const alertVariants = cva("flex gap-3 rounded-lg border-l-4 px-4 py-3 text-[0.9375rem] [&>svg]:mt-0.5 [&>svg]:size-5 [&>svg]:shrink-0", {
  variants: {
    tone: {
      neutral: "border-rule-strong bg-sunken text-ink",
      ok: "border-ok bg-ok-wash text-ink [&>svg]:text-ok",
      seal: "border-seal bg-seal-wash text-ink [&>svg]:text-seal",
      info: "border-binding bg-binding-wash text-ink [&>svg]:text-binding",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export function Alert({ className, tone, ...props }: ComponentProps<"div"> & VariantProps<typeof alertVariants>) {
  return <div className={cn(alertVariants({ tone }), className)} {...props} />;
}
