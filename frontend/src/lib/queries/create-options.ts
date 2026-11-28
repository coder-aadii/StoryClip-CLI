import type { FontOption } from "@/components/font-select-option";

/** What the create screen offers: fonts, caption templates and whether B-roll is configured. */
export const createOptionKeys = {
  fonts: ["fonts"] as const,
  captionTemplates: (includeFx: boolean) => ["caption-templates", includeFx ? "fx" : "standard"] as const,
  brollStatus: ["broll-status"] as const,
};

/** `GET /api/fonts`. Falls back to an empty list when unauthenticated or offline. */
export async function fetchFonts(): Promise<FontOption[]> {
  try {
    const response = await fetch("/api/fonts", { cache: "no-store" });
    if (!response.ok) return [];
    const data = (await response.json()) as { fonts?: FontOption[] };
    return data.fonts || [];
  } catch {
    return [];
  }
}

/** True only when the server has a stock footage provider; false on any failure. */
export async function fetchBrollConfigured(): Promise<boolean> {
  try {
    const response = await fetch("/api/broll/status");
    if (!response.ok) return false;
    const data = (await response.json()) as { configured?: boolean } | null;
    return data?.configured === true;
  } catch {
    return false;
  }
}
