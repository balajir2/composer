"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { CheckCircle2, XCircle, AlertTriangle } from "lucide-react";
import { Card, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { ComposerLogo } from "@/components/composer/composer-logo";

// Same Suspense-wrapping requirement as login/reset-password: useSearchParams()
// must live inside a <Suspense> boundary for Next.js 14 static generation.
export default function ApprovalResultPage() {
  return (
    <Suspense fallback={null}>
      <ApprovalResultContent />
    </Suspense>
  );
}

function ApprovalResultContent() {
  const params = useSearchParams();
  const status = params.get("status");

  const content =
    status === "approved"
      ? {
          Icon: CheckCircle2,
          color: "text-emerald-600",
          title: "Approved",
          message: "Your approval has been recorded. The workflow is continuing.",
        }
      : status === "rejected"
        ? {
            Icon: XCircle,
            color: "text-rose-600",
            title: "Rejected",
            message: "Your rejection has been recorded. The workflow has stopped here.",
          }
        : {
            Icon: AlertTriangle,
            color: "text-amber-600",
            title: "Link no longer valid",
            message:
              "This link has expired or the decision may already have been made. If you still need to act on this, open the run in Composer directly.",
          };

  const { Icon, color, title, message } = content;

  return (
    <Card className="w-full max-w-md shadow-lg">
      <CardHeader className="items-center text-center">
        <ComposerLogo size={48} className="mb-3 text-primary" />
        <Icon className={`size-12 ${color}`} aria-hidden />
        <CardTitle className="text-2xl">{title}</CardTitle>
        <CardDescription>{message}</CardDescription>
      </CardHeader>
    </Card>
  );
}
