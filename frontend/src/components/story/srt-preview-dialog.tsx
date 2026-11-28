"use client";

import { useEffect, useState } from "react";
import { Check, Copy, Download, FileText, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export function SrtPreviewDialog({
  open,
  onOpenChange,
  srtUrl,
  filename,
  title = "Subtitles (.srt)",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  srtUrl: string;
  filename: string;
  title?: string;
}) {
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open || !srtUrl) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setCopied(false);

    fetch(srtUrl, { credentials: "include" })
      .then(async (res) => {
        if (!res.ok) throw new Error(`Failed to load subtitles (${res.status})`);
        return res.text();
      })
      .then((text) => {
        if (!cancelled) setContent(text);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load subtitles");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, srtUrl]);

  async function handleCopy() {
    if (!content) return;
    await navigator.clipboard.writeText(content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 font-display text-base">
            <FileText className="size-4 text-brand" />
            {title}
          </DialogTitle>
        </DialogHeader>

        <div className="max-h-[55vh] overflow-y-auto rounded-xl border bg-background p-4">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-12 text-xs text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Loading subtitles...
            </div>
          ) : error ? (
            <p className="py-6 text-center text-xs text-destructive">{error}</p>
          ) : (
            <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-foreground">
              {content || "No subtitle entries found."}
            </pre>
          )}
        </div>

        <div className="flex items-center justify-end gap-2">
          <Button type="button" variant="outline" size="sm" onClick={handleCopy} disabled={!content || loading}>
            {copied ? <Check className="mr-1.5 size-3.5" /> : <Copy className="mr-1.5 size-3.5" />}
            {copied ? "Copied" : "Copy"}
          </Button>
          <a
            href={srtUrl}
            download={filename}
            className="inline-flex h-8 items-center gap-1.5 rounded-md bg-foreground px-3 text-xs font-medium text-background hover:bg-foreground/90"
          >
            <Download className="size-3.5" />
            Download .srt
          </a>
        </div>
      </DialogContent>
    </Dialog>
  );
}
