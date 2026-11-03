"use client";

export function CredentialRequestCard({ kind, detail }: { kind: string; detail: string }) {
  return (
    <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm">
      <p className="font-medium">Needs a credential: {kind}</p>
      <p className="text-muted-foreground">{detail}</p>
    </div>
  );
}
