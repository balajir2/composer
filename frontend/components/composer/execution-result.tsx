"use client";

import { Copy, FileDown, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function toText(output: unknown): string {
  return typeof output === "string" ? output : JSON.stringify(output, null, 2);
}

function downloadBlob(filename: string, mime: string, content: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Open a print-friendly copy of the result in a new window and auto-trigger
 * the browser's print dialog — the user chooses "Save as PDF" as the
 * destination.  Every modern browser ships a print-to-PDF backend, so this
 * works without bundling a PDF library.
 */
function downloadAsPdf(content: string, title: string): void {
  const w = window.open("", "_blank", "width=900,height=700");
  if (!w) {
    alert(
      "Couldn't open the print window — check that pop-ups are allowed for this site."
    );
    return;
  }
  const html = `<!doctype html>
<html><head>
<meta charset="utf-8"/>
<title>${escapeHtml(title)}</title>
<style>
  body { font-family: 'Plus Jakarta Sans', system-ui, sans-serif; padding: 2rem; max-width: 720px; margin: 0 auto; line-height: 1.55; color: #2e1869; }
  h1 { font-size: 1.5rem; border-bottom: 2px solid #872a95; padding-bottom: 0.4rem; margin-bottom: 1rem; }
  pre { white-space: pre-wrap; word-wrap: break-word; font-family: ui-monospace, SFMono-Regular, monospace; font-size: 0.85rem; background: #f3f3f5; padding: 1rem; border-radius: 6px; }
  @media print { body { padding: 1rem; } @page { margin: 0.6in; } }
</style>
</head><body>
<h1>${escapeHtml(title)}</h1>
<pre>${escapeHtml(content)}</pre>
<script>window.addEventListener('load', () => setTimeout(() => window.print(), 200));</script>
</body></html>`;
  w.document.open();
  w.document.write(html);
  w.document.close();
}

export function ExecutionResult({
  output,
  title = "Result",
}: {
  output: unknown;
  title?: string;
}) {
  const pretty = toText(output);
  const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle>{title}</CardTitle>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              downloadBlob(`${slug}-${ts}.md`, "text/markdown;charset=utf-8", pretty)
            }
            title="Download as Markdown (.md)"
          >
            <FileText className="mr-1 h-3.5 w-3.5" />
            Markdown
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => downloadAsPdf(pretty, title)}
            title="Open print preview — choose 'Save as PDF' in the dialog"
          >
            <FileDown className="mr-1 h-3.5 w-3.5" />
            PDF
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void navigator.clipboard.writeText(pretty);
            }}
            title="Copy result to clipboard"
          >
            <Copy className="mr-1 h-3.5 w-3.5" />
            Copy
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <pre className="bg-muted/30 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md p-3 text-sm">
          {pretty}
        </pre>
      </CardContent>
    </Card>
  );
}
