import { NextResponse } from "next/server";
import { auth } from "@/auth";

// Route-level enforcement of a pending forced password reset.  A session
// with `mustChangePassword` carries no usable Composer accessToken, so
// letting it render any protected page would only fail later when an API
// call 401s.  Redirect it to /change-password up front — except when it's
// already there, to avoid a redirect loop.
export default auth((req) => {
  if (req.auth?.mustChangePassword && req.nextUrl.pathname !== "/change-password") {
    return NextResponse.redirect(new URL("/change-password", req.nextUrl));
  }
});

export const config = {
  matcher: [
    "/((?!api|_next/static|_next/image|favicon.ico|login|register|forgot-password|reset-password).*)",
  ],
};
