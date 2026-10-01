import Image from "next/image";

// Icon + wordmark. The icon is provisional (public/brand/logo.svg).
export function EmbcoLogo({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`}>
      <Image src="/brand/logo.svg" alt="" width={36} height={36} className="size-9" priority />
      <span className="text-xl font-extrabold tracking-tight text-foreground">Embco</span>
    </span>
  );
}
