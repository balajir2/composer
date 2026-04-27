"use client";

import { useState } from "react";
import { Check, Copy, Globe } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

const apiBase = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

/**
 * Inline indicator for a published workflow's external invoke endpoint.
 *
 * Renders nothing when the workflow isn't published — keeps callers free
 * of conditional rendering at the call site.  When published, shows a
 * "Published" badge plus the full `POST /api/run/{slug}` URL with a
 * copy-to-clipboard button.  The full URL (origin-prefixed) is what
 * actually lands in the clipboard so users can paste straight into
 * curl / Postman / their own integration code without reassembling it.
 */
export function PublishedEndpoint({
  isProduction,
  externalSlug,
  variant = "inline",
}: {
  isProduction: boolean;
  externalSlug: string | null | undefined;
  /** "inline" for tight horizontal contexts (canvas top bar);
   *  "block" for settings-style stacked layouts. */
  variant?: "inline" | "block";
}) {
  const [copied, setCopied] = useState(false);

  if (!isProduction || !externalSlug) return null;

  const fullUrl = `${apiBase}/api/run/${externalSlug}`;

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(fullUrl);
      setCopied(true);
      toast.success("Endpoint URL copied to clipboard.");
      // Reset the icon after a moment so the copy affordance returns.
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Couldn't copy — your browser blocked clipboard access.");
    }
  }

  if (variant === "inline") {
    return (
      <div
        className="flex items-center gap-1.5 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1"
        title={`POST ${fullUrl}`}
      >
        <Globe className="h-3.5 w-3.5 text-emerald-700" />
        <Badge
          variant="default"
          className="border-emerald-300 bg-emerald-100 text-[10px] uppercase tracking-wide text-emerald-800 hover:bg-emerald-100"
        >
          Published
        </Badge>
        <code className="font-mono text-xs text-emerald-900">
          /api/run/{externalSlug}
        </code>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={handleCopy}
          aria-label="Copy endpoint URL"
          className="h-6 w-6 text-emerald-800 hover:bg-emerald-100"
        >
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        </Button>
      </div>
    );
  }

  // Block variant — stacked layout for the settings page.
  return (
    <div className="space-y-1.5 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2">
      <div className="flex items-center gap-2">
        <Globe className="h-4 w-4 text-emerald-700" />
        <Badge
          variant="default"
          className="border-emerald-300 bg-emerald-100 text-emerald-800 hover:bg-emerald-100"
        >
          Published
        </Badge>
      </div>
      <div className="flex items-center gap-2">
        <code className="flex-1 break-all rounded bg-white px-2 py-1 font-mono text-xs text-emerald-900">
          POST {fullUrl}
        </code>
        <Button
          variant="outline"
          size="sm"
          onClick={handleCopy}
          aria-label="Copy endpoint URL"
        >
          {copied ? (
            <>
              <Check className="mr-1.5 h-3.5 w-3.5" /> Copied
            </>
          ) : (
            <>
              <Copy className="mr-1.5 h-3.5 w-3.5" /> Copy
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
