import Image from "next/image";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  Globe,
  ImageIcon,
  Layout,
  Lock,
  Shield,
  Users,
  Wallet,
  Zap,
} from "lucide-react";
import { EmbcoLogo } from "@/components/brand/EmbcoLogo";
import { ThemeToggle } from "@/components/theme/ThemeToggle";

// Partner logos are monochrome-white in dark mode so they stay legible
const partnerLogo = "opacity-90 grayscale transition hover:opacity-100 hover:grayscale-0 dark:brightness-0 dark:invert";

const cardClass =
  "group relative overflow-hidden rounded-3xl border border-border bg-surface p-6 shadow-xl shadow-slate-200/50 transition-all hover:shadow-2xl md:p-10 dark:shadow-black/30";

// CTAs stay disabled until passkey sign-up (worker) and agency onboarding are migrated
function SoonButton({ children, variant }: { children: React.ReactNode; variant: "solid" | "outline" }) {
  const styles =
    variant === "solid"
      ? "bg-brand text-on-brand"
      : "border-2 border-brand bg-surface text-brand-text";
  return (
    <div className="relative">
      <button
        type="button"
        disabled
        className={`flex w-full cursor-not-allowed items-center justify-center gap-2 rounded-xl py-4 text-lg font-bold opacity-70 ${styles}`}
      >
        {children} <ArrowRight size={20} aria-hidden />
      </button>
      <span className="mt-2 block text-center text-xs font-semibold uppercase tracking-widest text-muted">
        Opening soon
      </span>
    </div>
  );
}

function StepHeader({ number, role }: { number: string; role: string }) {
  return (
    <div className="mb-5 flex items-center gap-3">
      <span className="font-mono text-sm font-bold text-brand-text">{number}</span>
      <span className="h-px flex-1 bg-border" />
      <span className="rounded-full border border-border px-2.5 py-0.5 text-xs font-semibold text-muted">{role}</span>
    </div>
  );
}

export default function Home() {
  return (
    <div className="min-h-dvh bg-background font-sans text-foreground selection:bg-brand-soft">
      {/* --- NAVBAR --- */}
      <nav className="sticky top-0 z-50 border-b border-border bg-surface/90 pt-safe backdrop-blur-md">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 md:h-20 md:px-6">
          <EmbcoLogo />

          <div className="hidden items-center space-x-8 text-sm font-medium text-muted md:flex">
            <a href="#how-it-works" className="transition-colors hover:text-brand-text">How it Works</a>
            <a href="#business" className="transition-colors hover:text-brand-text">For Business</a>
            <a href="#talent" className="transition-colors hover:text-brand-text">For Talent</a>
          </div>

          <div className="flex items-center gap-4">
            <div className="hidden items-center gap-2 lg:flex">
              <span className="size-2 animate-pulse rounded-full bg-accent" />
              <span className="text-[10px] font-bold uppercase tracking-widest text-muted">Protocol status: Online</span>
            </div>
            <ThemeToggle />
          </div>
        </div>
      </nav>

      {/* --- HERO SECTION --- */}
      <section className="relative overflow-hidden pt-14 pb-28 md:pt-20 md:pb-32">
        <div className="mx-auto max-w-7xl px-6 text-center">
          <div className="mb-8 inline-flex items-center gap-2 rounded-full border border-accent/20 bg-accent-soft px-4 py-1.5">
            <span className="size-2 animate-pulse rounded-full bg-accent" />
            <span className="text-xs font-bold uppercase tracking-widest text-accent-text">Live on Arc Testnet</span>
          </div>

          <h1 className="mx-auto mb-6 max-w-4xl text-4xl leading-[1.1] font-extrabold tracking-tight text-balance sm:text-5xl md:text-7xl">
            The Liquidity Layer for <br className="hidden sm:block" />
            <span className="text-brand-text">Digital Work</span>
          </h1>

          <p className="mx-auto mb-10 max-w-2xl text-lg leading-relaxed text-muted md:text-xl">
            Microtasks, Freelance, and AI Training. Paid instantly in USDC. <br className="hidden md:block" />
            Secure, global, and fee-efficient.
          </p>

          {/* Trust Row */}
          <div className="flex flex-row items-start justify-center gap-10 pb-8 md:gap-20">
            <div className="flex flex-col items-center">
              <span className="mb-3 flex h-4 items-center text-[11px] font-bold uppercase tracking-widest text-muted">
                Powered by
              </span>
              <div className="flex h-12 items-center justify-center">
                <Image src="/partners/arc.png" alt="Arc" width={480} height={165} className={`w-24 md:w-28 ${partnerLogo}`} />
              </div>
            </div>

            <div className="mt-2 hidden h-12 w-px bg-border md:block" />

            <div className="flex flex-col items-center">
              <span className="mb-3 flex h-4 items-center text-[11px] font-bold uppercase tracking-widest text-muted">
                Secured by
              </span>
              <div className="flex h-12 items-center justify-center">
                <Image src="/partners/circle.png" alt="Circle" width={480} height={123} className={`w-28 md:w-36 ${partnerLogo}`} />
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* --- THE SPLIT (Core User Types) --- */}
      <section className="mx-auto -mt-12 max-w-7xl px-4 pb-24 md:px-6">
        <div className="grid gap-6 md:grid-cols-2 md:gap-8">
          {/* TALENT CARD */}
          <div className={`${cardClass} hover:shadow-accent/10`}>
            <div className="absolute top-0 right-0 p-8 opacity-10 transition-opacity group-hover:opacity-20">
              <Users size={120} className="translate-x-8 -translate-y-8 -rotate-12 text-brand-text" aria-hidden />
            </div>

            <div className="relative z-10">
              <div className="mb-6 flex size-14 items-center justify-center rounded-2xl bg-brand-soft text-brand-text">
                <Users size={28} aria-hidden />
              </div>
              <h2 className="mb-3 text-2xl font-bold md:text-3xl">I want to Earn</h2>
              <p className="mb-8 text-lg leading-relaxed text-muted">
                Turn your time into digital dollars. Access global tasks, train AI models, and get paid instantly with no
                minimums.
              </p>

              <ul className="mb-10 space-y-3">
                {["Instant USDC Settlements", "No Bank Account Required", "Build On-Chain Reputation"].map((item) => (
                  <li key={item} className="flex items-center font-medium text-muted">
                    <CheckCircle2 size={18} className="mr-3 shrink-0 text-accent-text" aria-hidden />
                    {item}
                  </li>
                ))}
              </ul>

              <SoonButton variant="solid">Join Workforce</SoonButton>
            </div>
          </div>

          {/* BUSINESS CARD */}
          <div className={`${cardClass} hover:shadow-brand/10`}>
            <div className="absolute top-0 right-0 p-8 opacity-10 transition-opacity group-hover:opacity-20">
              <Layout size={120} className="translate-x-8 -translate-y-8 -rotate-12 text-brand-text" aria-hidden />
            </div>

            <div className="relative z-10">
              <div className="mb-6 flex size-14 items-center justify-center rounded-2xl bg-brand-soft text-brand-text">
                <Layout size={28} aria-hidden />
              </div>
              <h2 className="mb-3 text-2xl font-bold md:text-3xl">I want to Hire</h2>
              <p className="mb-8 text-lg leading-relaxed text-muted">
                Crowdsource globally via API or UI. Scale your data labeling and microtasks with programmatic payments.
              </p>

              <ul className="mb-10 space-y-3">
                {["API-First Integration", "Automated QA & Consensus", "Pay only for Valid Work"].map((item) => (
                  <li key={item} className="flex items-center font-medium text-muted">
                    <CheckCircle2 size={18} className="mr-3 shrink-0 text-brand-text" aria-hidden />
                    {item}
                  </li>
                ))}
              </ul>

              <SoonButton variant="outline">Create Campaign</SoonButton>
            </div>
          </div>
        </div>
      </section>

      {/* --- HOW IT WORKS: one campaign followed end to end, each step shows the real product --- */}
      <section id="how-it-works" className="scroll-mt-24 border-t border-border bg-surface py-20 md:py-24">
        <div className="mx-auto max-w-7xl px-6">
          <div className="mb-14 max-w-2xl">
            <div className="mb-6 inline-block rounded-full bg-brand-soft px-3 py-1 text-xs font-bold uppercase tracking-widest text-brand-text">
              How it Works
            </div>
            <h2 className="mb-4 text-3xl font-extrabold tracking-tight md:text-4xl">
              From a funded campaign to a paid worker.
            </h2>
            <p className="text-lg leading-relaxed text-muted">
              Follow one labeling campaign through Embco. Every step happens in the app, and every payment lands
              on-chain.
            </p>
          </div>

          <ol className="grid gap-6 md:grid-cols-3">
            {/* Step 1: agency funds the campaign */}
            <li className="flex flex-col rounded-3xl border border-border bg-background p-6">
              <StepHeader number="01" role="Agency" />
              <h3 className="mb-2 text-xl font-bold">Fund a campaign</h3>
              <p className="mb-6 leading-relaxed text-muted">
                Upload your data, set a reward per task and deposit USDC. Funds stay locked in escrow until work is
                approved.
              </p>

              <div aria-hidden className="mt-auto rounded-2xl border border-border bg-surface p-4">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <span className="font-semibold">Product image labeling</span>
                  <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-bold text-accent-text">
                    Active
                  </span>
                </div>
                <div className="mb-4 grid grid-cols-2 gap-2 text-sm">
                  <div className="rounded-xl bg-surface-2 px-3 py-2">
                    <div className="text-xs text-muted">Tasks</div>
                    <div className="font-bold">500</div>
                  </div>
                  <div className="rounded-xl bg-surface-2 px-3 py-2">
                    <div className="text-xs text-muted">Reward</div>
                    <div className="font-bold">0.15 USDC</div>
                  </div>
                </div>
                <div className="flex items-center gap-2 text-sm">
                  <Lock size={16} className="shrink-0 text-brand-text" />
                  <span className="text-muted">In escrow</span>
                  <span className="ml-auto font-bold">78.75 USDC</span>
                </div>
              </div>
            </li>

            {/* Step 2: worker completes a task */}
            <li className="flex flex-col rounded-3xl border border-border bg-background p-6">
              <StepHeader number="02" role="Worker" />
              <h3 className="mb-2 text-xl font-bold">Complete tasks</h3>
              <p className="mb-6 leading-relaxed text-muted">
                Workers pick up tasks from their phone and answer in seconds. Golden questions and consensus keep
                quality high.
              </p>

              <div aria-hidden className="mt-auto rounded-2xl border border-border bg-surface p-4">
                <div className="mb-3 flex h-20 items-center justify-center rounded-xl bg-surface-2 text-muted">
                  <ImageIcon size={28} />
                </div>
                <div className="mb-3 text-sm font-semibold">Does this image contain a car?</div>
                <div className="grid grid-cols-2 gap-2 text-sm font-semibold">
                  <span className="flex h-10 items-center justify-center gap-1.5 rounded-xl bg-brand text-on-brand">
                    <Check size={16} /> Yes
                  </span>
                  <span className="flex h-10 items-center justify-center rounded-xl border border-border text-muted">
                    No
                  </span>
                </div>
              </div>
            </li>

            {/* Step 3: payout on approval */}
            <li className="flex flex-col rounded-3xl border border-border bg-background p-6">
              <StepHeader number="03" role="Payout" />
              <h3 className="mb-2 text-xl font-bold">Get paid on approval</h3>
              <p className="mb-6 leading-relaxed text-muted">
                Once an answer is approved, the reward goes straight to the worker&apos;s wallet in USDC. Anyone can
                verify the payment on-chain.
              </p>

              <div aria-hidden className="mt-auto space-y-3 rounded-2xl border border-border bg-surface p-4">
                <div className="flex items-center gap-3">
                  <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-text">
                    <CheckCircle2 size={20} />
                  </span>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold whitespace-nowrap">Task approved</div>
                    <div className="truncate text-xs text-muted">#892 · Image labeling</div>
                  </div>
                  <span className="ml-auto shrink-0 font-bold text-accent-text">+0.15 USDC</span>
                </div>
                <div className="flex items-center justify-between gap-2 rounded-xl bg-surface-2 px-3 py-2 font-mono text-xs text-muted">
                  <span className="truncate">tx 0x8f3a…c21d</span>
                  <span className="shrink-0">Arc Testnet</span>
                </div>
              </div>
            </li>
          </ol>
        </div>
      </section>

      {/* --- FOR BUSINESS (Enterprise Infrastructure) --- */}
      <section id="business" className="scroll-mt-24 border-t border-border bg-background py-20 md:py-24">
        <div className="mx-auto max-w-7xl px-6">
          <div className="flex flex-col items-center gap-16 md:flex-row">
            <div className="flex-1">
              <div className="mb-6 inline-block rounded-full bg-brand-soft px-3 py-1 text-xs font-bold uppercase tracking-widest text-brand-text">
                Enterprise Infrastructure
              </div>
              <h2 className="mb-6 text-3xl font-extrabold md:text-4xl">Scale with Confidence.</h2>
              <p className="mb-8 text-lg leading-relaxed text-muted">
                Whether you&apos;re a startup or an enterprise, Embco provides the liquidity layer to manage a global
                workforce.
              </p>

              <div className="space-y-6">
                {[
                  { Icon: Layout, title: "Flexible Launch", text: "Launch via our intuitive UI Dashboard or integrate directly via API." },
                  { Icon: CheckCircle2, title: "Smart Validation", text: "Pay only for valid results. Set consensus rules automatically." },
                  { Icon: Zap, title: "Direct Settlement", text: "Funds go directly to workers. No agency markups." },
                ].map(({ Icon, title, text }) => (
                  <div key={title} className="flex gap-4">
                    <div className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-surface text-brand-text shadow-sm">
                      <Icon size={24} aria-hidden />
                    </div>
                    <div>
                      <h4 className="text-lg font-bold">{title}</h4>
                      <p className="text-muted">{text}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Campaign mock */}
            <div className="w-full flex-1 rounded-3xl border border-border bg-surface p-6 shadow-2xl md:p-8 dark:shadow-black/30">
              <div className="space-y-4">
                <div className="h-4 w-1/3 animate-pulse rounded bg-surface-2" />
                <div className="mb-8 h-8 w-3/4 animate-pulse rounded bg-surface-2" />
                <div className="space-y-2">
                  <div className="flex h-12 w-full items-center rounded-xl border border-brand/20 bg-brand-soft px-4">
                    <span className="mr-3 size-2 rounded-full bg-brand" />
                    <span className="text-sm font-medium">Campaign: Data Labeling V1</span>
                  </div>
                  <div className="flex h-12 w-full items-center rounded-xl border border-accent/20 bg-accent-soft px-4">
                    <span className="mr-3 size-2 rounded-full bg-accent" />
                    <span className="text-sm font-medium">Status: 98% Validated</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* --- FOR TALENT (Work Your Way) --- */}
      <section id="talent" className="scroll-mt-24 border-t border-border bg-surface py-20 md:py-24">
        <div className="mx-auto max-w-7xl px-6">
          <div className="flex flex-col items-center gap-16 md:flex-row-reverse">
            <div className="flex-1">
              <div className="mb-6 inline-block rounded-full bg-accent-soft px-3 py-1 text-xs font-bold uppercase tracking-widest text-accent-text">
                For Talent
              </div>
              <h2 className="mb-6 text-3xl font-extrabold md:text-4xl">Work Freedom.</h2>
              <p className="mb-8 text-lg leading-relaxed text-muted">
                Sign up with your fingerprint or Face ID and start earning. No bank accounts, no minimums, no waiting.
              </p>

              <div className="space-y-6">
                {[
                  { Icon: Wallet, title: "Instant Global Payments", text: "Receive USDC directly to your wallet immediately after approval." },
                  { Icon: Shield, title: "Reputation Passport", text: "Your work history is stored on-chain. Own your professional identity." },
                  { Icon: Globe, title: "Work Anywhere", text: "Accessible from 150+ countries. Permissionless access." },
                ].map(({ Icon, title, text }) => (
                  <div key={title} className="flex gap-4">
                    <div className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-background text-accent-text shadow-sm">
                      <Icon size={24} aria-hidden />
                    </div>
                    <div>
                      <h4 className="text-lg font-bold">{title}</h4>
                      <p className="text-muted">{text}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Balance mock */}
            <div className="relative w-full flex-1 overflow-hidden rounded-3xl border border-border bg-surface p-6 shadow-2xl md:p-8 dark:shadow-black/30">
              <div className="absolute top-0 right-0 p-12 text-accent opacity-5">
                <Zap size={140} aria-hidden />
              </div>
              <div className="relative z-10">
                <div className="mb-1 text-sm uppercase tracking-widest text-muted">Current Balance</div>
                <div className="mb-2 font-mono text-4xl font-bold md:text-5xl">$845.50</div>
                <div className="mb-8 inline-flex items-center gap-2 rounded-full border border-accent/20 bg-accent-soft px-3 py-1 text-xs font-bold text-accent-text">
                  <span className="size-1.5 animate-pulse rounded-full bg-accent" />
                  Paid instantly
                </div>

                <div className="space-y-3">
                  {[
                    ["Data Labeling Task #892", "+$2.50 USDC"],
                    ["Translation Task #104", "+$15.00 USDC"],
                  ].map(([task, amount]) => (
                    <div key={task} className="flex items-center justify-between gap-3 rounded-xl border border-border bg-background p-3">
                      <span className="text-sm text-muted">{task}</span>
                      <span className="shrink-0 text-sm font-bold text-accent-text">{amount}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* --- WHY EMBCO --- */}
      <section className="border-t border-border bg-surface py-20 md:py-24">
        <div className="mx-auto max-w-7xl px-6">
          <div className="mx-auto max-w-4xl text-center">
            <h2 className="mb-12 text-3xl font-extrabold tracking-tight md:text-5xl">
              Build faster. Scale globally. <br />
              <span className="text-brand-text">Pay transparently.</span>
            </h2>
          </div>

          <div className="grid gap-8 divide-y divide-border text-center md:grid-cols-3 md:divide-x md:divide-y-0">
            {[
              ["Borderless", "Access talent in 150+ countries instantly."],
              ["Transparent", "Every payment is verifiable on-chain."],
              ["Cost Efficient", "Save ~30% vs traditional BPO firms."],
            ].map(([title, text]) => (
              <div key={title} className="px-6 py-4">
                <h3 className="mb-2 text-2xl font-bold">{title}</h3>
                <p className="text-muted">{text}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* --- FOOTER --- */}
      <footer className="border-t border-border bg-background py-12 pb-safe text-center">
        <p className="pb-4 text-xs font-bold uppercase tracking-widest text-muted">
          © 2026 Embco. Powered by Arc Network.
        </p>
      </footer>
    </div>
  );
}
