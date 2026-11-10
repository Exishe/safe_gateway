// Icons drawn for this project: no icon package, nothing to license.
import type { ComponentProps } from "react";

function Svg(props: ComponentProps<"svg">) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    />
  );
}

/** The mark: a folder tab over a struck line, the gesture of a redacted file. */
export function Mark(props: ComponentProps<"svg">) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" focusable="false" {...props}>
      <rect x="3" y="7" width="26" height="20" rx="3" fill="currentColor" opacity="0.14" />
      <path d="M3 10a3 3 0 0 1 3-3h7l3 3h10a3 3 0 0 1 3 3" fill="none" stroke="currentColor" strokeWidth="2" />
      <rect x="8" y="16" width="16" height="4" rx="1" fill="currentColor" />
    </svg>
  );
}

export const ShieldCheck = (p: ComponentProps<"svg">) => (
  <Svg {...p}>
    <path d="M12 3L5 6V11C5 15.5 8 19.3 12 21C16 19.3 19 15.5 19 11V6Z" />
    <path d="m9 12 2 2 4-4" />
  </Svg>
);

export const ShieldBroken = (p: ComponentProps<"svg">) => (
  <Svg {...p}>
    <path d="M12 3L5 6V11C5 15.5 8 19.3 12 21C16 19.3 19 15.5 19 11V6Z" />
    <path d="m13 7-3 5h4l-3 5" />
  </Svg>
);

export const Info = (p: ComponentProps<"svg">) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v5M12 8h.01" />
  </Svg>
);

export const Warning = (p: ComponentProps<"svg">) => (
  <Svg {...p}>
    <path d="M12 4 2.8 19.5h18.4z" />
    <path d="M12 10v4.5M12 17h.01" />
  </Svg>
);

export const FileText = (p: ComponentProps<"svg">) => (
  <Svg {...p}>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
    <path d="M14 3v5h5M9 13h6M9 17h6" />
  </Svg>
);

export const Copy = (p: ComponentProps<"svg">) => (
  <Svg {...p}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15V6a2 2 0 0 1 2-2h8" />
  </Svg>
);

export const Refresh = (p: ComponentProps<"svg">) => (
  <Svg {...p}>
    <path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4" />
  </Svg>
);

export const Send = (p: ComponentProps<"svg">) => (
  <Svg {...p}>
    <path d="M4 12 20 4l-5 16-3-7z" />
    <path d="m12 13 8-9" />
  </Svg>
);

export const Stop = (p: ComponentProps<"svg">) => (
  <Svg {...p}>
    <rect x="6" y="6" width="12" height="12" rx="1.5" />
  </Svg>
);

export const Sun = (p: ComponentProps<"svg">) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </Svg>
);

export const Moon = (p: ComponentProps<"svg">) => (
  <Svg {...p}>
    <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
  </Svg>
);

export const Download = (p: ComponentProps<"svg">) => (
  <Svg {...p}>
    <path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14" />
  </Svg>
);

export const Monitor = (p: ComponentProps<"svg">) => (
  <Svg {...p}>
    <rect x="3" y="4" width="18" height="12" rx="2" />
    <path d="M8 20h8M12 16v4" />
  </Svg>
);
