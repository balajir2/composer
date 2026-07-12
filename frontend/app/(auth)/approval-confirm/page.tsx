"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { CheckCircle2, XCircle, AlertTriangle } from "lucide-react";
import { Card, CardHeader, CardTitle, CardDescription, CardFooter } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ComposerLogo } from "@/components/composer/composer-logo";

const apiBase = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

// P1-1: this page exists so the decision is a deliberate POST (a real
// form submission from a user click), never a bare GET. Email security
// scanners and preview services routinely follow GET links automatically
// to check for malware/phishing — a state-changing GET would let the
// first scanner-opened link silently make the decision before a human
// ever sees it. GET /approvals/email/{token} only validates and lands
// here; this form's POST to /approvals/email/{token}/confirm is what
// actually resolves the approval.
export default function ApprovalConfirmPage() {
  return (
    <Suspense fallback={null}>
      <ApprovalConfirmContent />
    </Suspense>
  );
}

function ApprovalConfirmContent() {
  const params = useSearchParams();
  const token = params.get("token");
  const decision = params.get("decision");
  const [submitting, setSubmitting] = useState(false);

  if (!token || (decision !== "approved" && decision !== "rejected")) {
    return (
      <Card className="w-full max-w-md shadow-lg">
        <CardHeader className="items-center text-center">
          <ComposerLogo size={48} className="mb-3 text-primary" />
          <AlertTriangle className="size-12 text-amber-600" aria-hidden />
          <CardTitle className="text-2xl">Link no longer valid</CardTitle>
          <CardDescription>
            This link has expired or the decision may already have been made. If you still
            need to act on this, open the run in Composer directly.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const isApprove = decision === "approved";
  const Icon = isApprove ? CheckCircle2 : XCircle;
  const color = isApprove ? "text-emerald-600" : "text-rose-600";
  const verb = isApprove ? "approve" : "reject";

  return (
    <Card className="w-full max-w-md shadow-lg">
      <CardHeader className="items-center text-center">
        <ComposerLogo size={48} className="mb-3 text-primary" />
        <Icon className={`size-12 ${color}`} aria-hidden />
        <CardTitle className="text-2xl capitalize">Confirm {verb}</CardTitle>
        <CardDescription>
          You&apos;re about to <strong>{verb}</strong> this workflow step. This action resumes
          the workflow and can&apos;t be undone from this page.
        </CardDescription>
      </CardHeader>
      <CardFooter className="justify-center">
        <form
          method="POST"
          action={`${apiBase}/approvals/email/${encodeURIComponent(token)}/confirm`}
          onSubmit={() => setSubmitting(true)}
        >
          <Button type="submit" disabled={submitting} variant={isApprove ? "default" : "outline"}>
            {submitting ? "Submitting…" : `Confirm ${verb}`}
          </Button>
        </form>
      </CardFooter>
    </Card>
  );
}
