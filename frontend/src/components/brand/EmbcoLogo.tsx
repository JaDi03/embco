// Icon + wordmark. The mark is drawn inline with theme tokens so it has no background
// and adapts to light and dark mode. App icons and the favicon use public/brand/logo.svg.
export function EmbcoLogo({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <svg
        viewBox="148 124 216 264"
        className="h-8 w-auto"
        fill="none"
        strokeWidth={52}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M 338 150 L 174 150 L 174 362 L 338 362" className="stroke-brand-text" />
        <path d="M 234 262 L 264 292 L 328 228" className="stroke-accent" />
      </svg>
      <span className="text-2xl font-extrabold tracking-tight text-foreground">Embco</span>
    </span>
  );
}
