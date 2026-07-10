"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { signIn, getSession } from "next-auth/react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ComposerLogo } from "@/components/composer/composer-logo";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { toast } from "sonner";

// Next.js 14 production builds require any component reading
// useSearchParams() to live inside a <Suspense> boundary so the static
// generator can defer it without breaking pre-render.  Wrap the form
// at the page level; the inner LoginForm reads the search params.

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const router = useRouter();
  const params = useSearchParams();
  const returnTo = params.get("returnTo") ?? "/";

  const azureEnabled = process.env.NEXT_PUBLIC_AZURE_SSO_ENABLED === "true";

  async function onCredentialsSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    const res = await signIn("credentials", {
      email,
      password,
      redirect: false,
    });
    if (res?.error) {
      setSubmitting(false);
      toast.error("Sign-in failed. Check your credentials.");
      return;
    }
    const session = await getSession();
    setSubmitting(false);
    if (session?.mustChangePassword) {
      router.push("/change-password");
      return;
    }
    router.push(returnTo);
  }

  return (
    <Card className="w-full max-w-md shadow-lg">
      <CardHeader className="items-center text-center">
        <ComposerLogo size={48} className="mb-3 text-primary" />
        <CardTitle className="text-2xl">Sign in to Composer</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        {azureEnabled && (
          <>
            <Button
              className="w-full"
              variant="outline"
              onClick={() => signIn("azure-ad", { callbackUrl: returnTo })}
            >
              Continue with Azure
            </Button>
            <div className="flex items-center gap-4">
              <Separator className="flex-1" />
              <span className="text-xs text-muted-foreground">OR</span>
              <Separator className="flex-1" />
            </div>
          </>
        )}
        <form onSubmit={onCredentialsSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="password">Password</Label>
              <Link
                href="/forgot-password"
                className="text-xs text-muted-foreground underline-offset-4 hover:underline"
              >
                Forgot password?
              </Link>
            </div>
            <Input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </div>
          <Button type="submit" className="w-full" disabled={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
          </Button>
        </form>
        <p className="text-center text-sm text-muted-foreground">
          New to Composer?{" "}
          <Link href="/register" className="text-primary underline-offset-4 hover:underline">
            Create an account
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
