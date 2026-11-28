"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Clapperboard, CornerDownLeft, Film, Loader2, LogOut, Plus, Scissors, Search, Settings, Sparkles,
} from "lucide-react";
import { signOut, useSession } from "@/lib/auth-client";
import { timeAgo, type GenerationSummary } from "@/lib/generations";
import { useGenerations } from "@/lib/queries/generations";
import { StatusBadge } from "@/components/app/status-badge";
import { ThemeToggle } from "@/components/theme-toggle";
import { useAppTheme } from "@/components/theme-provider";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Kbd } from "@/components/ui/kbd";
import { cn } from "@/lib/utils";
import type { Workflow } from "@/lib/workflows";

type NavItem = { href: string; labelKey: "storyClip" | "videosToClip" | "library" | "settings"; icon: typeof Film; match: (path: string) => boolean };

const NAV: NavItem[] = [
  { href: "/", labelKey: "storyClip", icon: Clapperboard, match: (p) => p === "/" },
  { href: "/clip", labelKey: "videosToClip", icon: Scissors, match: (p) => p.startsWith("/clip") || p.startsWith("/tasks") },
  { href: "/list", labelKey: "library", icon: Film, match: (p) => p.startsWith("/list") },
  { href: "/settings", labelKey: "settings", icon: Settings, match: (p) => p === "/settings" || p.startsWith("/settings/") },
];

/** What the create screen tells the tour host; other pages need nothing. */
export type ShellTour = {
  /** The user is doing something: don't start a tour, and leave one that's showing. */
  busy: boolean;
  onWorkflow?: (workflow: Workflow) => void;
  onEnd?: () => void;
  /** Open a section of the composer's settings menu for a step that points into it. */
  onSettingsSection?: (id: string) => void;
};

/** Application chrome: requires unified better-auth session, redirects unauthenticated users to /sign-in. */
export function AppShell({ children }: { children: React.ReactNode; tour?: ShellTour }) {
  const t = useTranslations("shell");
  const { data: session, isPending } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const immersive = /^\/tasks\/[^/]+\/edit/.test(pathname);
  const compact = immersive;
  const user = session?.user;

  useEffect(() => {
    if (!isPending && !user) {
      const next = pathname && pathname !== "/" ? `?next=${encodeURIComponent(pathname)}` : "";
      router.replace(`/sign-in${next}`);
    }
  }, [isPending, user, pathname, router]);

  useEffect(() => {
    if (!user) return;
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
        return;
      }
      const target = event.target as HTMLElement | null;
      const typing = target?.closest("input, textarea, select, [contenteditable=true], [role=dialog]");
      if (!typing && !event.metaKey && !event.ctrlKey && !event.altKey && event.key.toLowerCase() === "n") {
        event.preventDefault();
        router.push("/");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router, user]);

  useAppTheme(Boolean(user));

  if (isPending || !user) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-canvas">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  async function handleLogout() {
    await signOut();
    router.replace("/sign-in");
  }

  const items = NAV.map((item) => ({ ...item, label: t(`nav.${item.labelKey}`) }));
  const mobileNav = items;

  return (
    <div data-app-shell="" className="flex min-h-dvh bg-canvas">
      <aside
        className={cn(
          "sticky top-0 hidden h-dvh shrink-0 flex-col border-r border-sidebar-border bg-sidebar",
          !immersive && "md:flex",
          compact ? "w-16 items-center px-2" : "w-60 px-3",
        )}
      >
        <Link href="/" className={cn("flex h-14 items-center gap-2 font-display text-lg font-bold tracking-tight", compact ? "justify-center" : "px-2")}>
          <Image src="/logo.png" alt="Story Clip Studio" width={24} height={24} unoptimized className="size-6 shrink-0 object-contain" priority />
          {!compact && (
            <Image src="/text_logo.png" alt="Story Clip Studio" width={132} height={24} unoptimized className="h-5 w-auto object-contain" priority />
          )}
        </Link>

        <SidebarLink compact={compact} href="/" label={t("nav.newGeneration")} shortcut="N" className="mb-4 mt-1 bg-foreground text-background hover:bg-foreground/90 hover:text-background">
          <Plus className="size-4" />
        </SidebarLink>

        <nav aria-label={t("search.mainNav")} className="flex w-full flex-col gap-0.5">
          {items.map((item) => {
            const active = item.match(pathname);
            return (
              <SidebarLink key={item.href} compact={compact} href={item.href} label={item.label} active={active}>
                <item.icon className="size-4" />
              </SidebarLink>
            );
          })}
        </nav>

        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          className={cn(
            "mt-4 flex h-9 w-full items-center gap-2 rounded-lg border border-sidebar-border bg-background text-sm text-muted-foreground transition-colors hover:text-foreground",
            compact ? "justify-center px-0" : "px-2.5",
          )}
          aria-label={t("search.generations")}
        >
          <Search className="size-4 shrink-0" />
          {!compact && <><span className="flex-1 text-left">{t("search.label")}</span><Kbd>⌘K</Kbd></>}
        </button>

        <div className="mt-auto w-full pb-3">
          <div className={cn("flex items-center rounded-lg border border-sidebar-border/70 bg-background/60 px-2.5 py-2", compact ? "justify-center px-0" : "justify-between gap-2")}>
            {!compact && (
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium text-foreground">{user.name || user.email}</p>
                <p className="truncate text-[10px] text-muted-foreground">{user.email}</p>
              </div>
            )}
            <div className="flex items-center gap-1">
              <ThemeToggle tooltipSide="right" className="rounded-md p-1.5 hover:bg-accent [&_svg]:size-4" />
              <button
                type="button"
                onClick={handleLogout}
                title="Sign out"
                aria-label="Sign out"
                className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <LogOut className="size-4" />
              </button>
            </div>
          </div>
        </div>
      </aside>

      <div className={cn("flex min-w-0 flex-1 flex-col", !immersive && "pb-[calc(4rem+env(safe-area-inset-bottom))] md:pb-0")}>
        {!immersive ? <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b bg-background/80 px-4 backdrop-blur md:hidden">
          <Link href="/" className="flex items-center gap-2 font-display text-lg font-bold tracking-tight">
            <Image src="/logo.png" alt="Story Clip Studio" width={22} height={22} unoptimized className="size-[22px] object-contain" />
            <Image src="/text_logo.png" alt="Story Clip Studio" width={120} height={22} unoptimized className="h-5 w-auto object-contain" />
          </Link>
          <div className="flex items-center gap-1">
            <ThemeToggle tooltipSide="bottom" className="rounded-lg p-2 hover:bg-accent [&_svg]:size-5" />
            <button type="button" onClick={() => setPaletteOpen(true)} aria-label={t("search.generations")} className="rounded-lg p-2 text-muted-foreground hover:bg-accent">
              <Search className="size-5" />
            </button>
          </div>
        </header> : null}
        {children}
      </div>

      {!immersive ? <nav aria-label={t("search.mobileNav")} style={{ gridTemplateColumns: `repeat(${mobileNav.length}, minmax(0, 1fr))` }} className="fixed inset-x-0 bottom-0 z-40 grid border-t bg-background/90 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        {mobileNav.map((item) => {
          const active = item.match(pathname);
          return (
            <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined}
              className={cn("flex h-16 flex-col items-center justify-center gap-1 text-[11px] font-medium", active ? "text-foreground" : "text-muted-foreground")}>
              <item.icon className={cn("size-5", active && "text-brand")} />{item.label}
            </Link>
          );
        })}
      </nav> : null}

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}

function SidebarLink({ href, label, active, compact, shortcut, className, tour, children }: {
  href: string; label: string; active?: boolean; compact: boolean; shortcut?: string; className?: string; /** `data-tour` target for the welcome tour. */ tour?: string; children: React.ReactNode;
}) {
  const link = (
    <Link
      data-tour={tour}
      href={href}
      aria-current={active ? "page" : undefined}
      aria-label={compact ? label : undefined}
      className={cn(
        "flex h-9 w-full items-center gap-2.5 rounded-lg text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        compact ? "justify-center" : "px-2.5",
        active ? "bg-background text-foreground shadow-xs ring-1 ring-sidebar-border" : "text-muted-foreground hover:bg-sidebar-accent hover:text-foreground",
        className,
      )}
    >
      {children}
      {!compact && <span className="flex-1">{label}</span>}
      {!compact && shortcut && <span className="text-[11px] opacity-60">{shortcut}</span>}
    </Link>
  );
  if (!compact) return link;
  return <Tooltip><TooltipTrigger asChild>{link}</TooltipTrigger><TooltipContent side="right">{label}</TooltipContent></Tooltip>;
}

type PaletteEntry = { id: string; label: string; hint?: string; href: string; icon: typeof Film; generation?: GenerationSummary };

const NO_GENERATIONS: GenerationSummary[] = [];

function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useTranslations("shell");
  const locale = useLocale();
  const router = useRouter();
  const [query, setQuery] = useState("");
  const { data: session } = useSession();
  // Disabled while closed; opening the palette refetches the list.
  const generations: GenerationSummary[] = useGenerations(open ? session?.user?.id : undefined).data ?? NO_GENERATIONS;
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
  }, [open]);

  const entries = useMemo<PaletteEntry[]>(() => {
    const q = query.trim().toLowerCase();
    const actions: PaletteEntry[] = [
      { id: "story", label: t("nav.storyClip"), hint: "Create a narrated video from an idea or script", href: "/", icon: Sparkles },
      { id: "clip", label: t("nav.videosToClip"), hint: t("palette.newHint"), href: "/clip", icon: Scissors },
      { id: "library", label: t("palette.library"), href: "/list", icon: Film },
      { id: "settings", label: t("nav.settings"), href: "/settings", icon: Settings },
    ].filter((entry) => !q || entry.label.toLowerCase().includes(q));
    const matches = generations
      .filter((generation) => !q || generation.source_title.toLowerCase().includes(q))
      .slice(0, 8)
      .map((generation) => ({
        id: generation.id, label: generation.source_title, hint: timeAgo(generation.created_at, locale),
        href: `/tasks/${generation.id}`, icon: Film, generation,
      }));
    return [...actions, ...matches];
  }, [generations, query, t, locale]);

  const go = useCallback((entry: PaletteEntry | undefined) => {
    if (!entry) return;
    onOpenChange(false);
    router.push(entry.href);
  }, [onOpenChange, router]);

  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false} className="top-[18%] max-w-xl translate-y-0 gap-0 overflow-hidden p-0">
        <DialogTitle className="sr-only">{t("search.dialog")}</DialogTitle>
        <div className="flex items-center gap-3 border-b px-4">
          <Search className="size-4 text-muted-foreground" />
          <input
            autoFocus
            value={query}
            onChange={(event) => { setQuery(event.target.value); setActive(0); }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") { event.preventDefault(); setActive((i) => Math.min(entries.length - 1, i + 1)); }
              if (event.key === "ArrowUp") { event.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
              if (event.key === "Enter") { event.preventDefault(); go(entries[active]); }
            }}
            placeholder={t("search.placeholder")}
            aria-label={t("search.inputLabel")}
            className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          <Kbd>esc</Kbd>
        </div>
        <div ref={listRef} className="max-h-80 overflow-y-auto p-1.5" role="listbox">
          {entries.length === 0 && <p className="px-3 py-8 text-center text-sm text-muted-foreground">{t("search.empty", { query })}</p>}
          {entries.map((entry, index) => (
            <button
              key={entry.id}
              type="button"
              role="option"
              aria-selected={index === active}
              data-index={index}
              onMouseMove={() => setActive(index)}
              onClick={() => go(entry)}
              className={cn("flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm", index === active && "bg-accent")}
            >
              <entry.icon className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">{entry.label}</span>
              {entry.generation ? <StatusBadge status={entry.generation.status} /> : null}
              {entry.hint && <span className="shrink-0 text-xs text-muted-foreground">{entry.hint}</span>}
              {index === active && <CornerDownLeft className="size-3.5 text-muted-foreground" />}
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
