import { Music3, Waypoints } from "lucide-react";

export function ComposerLogo({
  size = 32,
  className,
}: {
  size?: number;
  className?: string;
}) {
  return (
    <span
      className={className}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: Math.round(size * 0.15),
      }}
      aria-label="Composer"
    >
      <Music3 size={size} aria-hidden />
      <Waypoints size={size} aria-hidden />
    </span>
  );
}
