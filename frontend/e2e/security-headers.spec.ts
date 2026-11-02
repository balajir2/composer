import { test, expect } from "@playwright/test";

// API-level (no browser): asserts the production header policy on real
// responses.  Meaningful against a production build (`next start`), since
// HSTS and the no-unsafe-eval CSP are production-only.
const routes = ["/", "/login", "/designer"];

for (const route of routes) {
  test(`security headers present on ${route}`, async ({ request }) => {
    const res = await request.get(route, { maxRedirects: 0 });
    // Signed-out protected routes may 3xx; headers must be on that response too.
    expect([200, 307, 308, 302, 303]).toContain(res.status());
    const h = res.headers();
    expect(h["x-frame-options"]).toBe("DENY");
    expect(h["x-content-type-options"]).toBe("nosniff");
    expect(h["referrer-policy"]).toBe("strict-origin-when-cross-origin");
    expect(h["strict-transport-security"]).toBe("max-age=31536000; includeSubDomains");
    expect(h["permissions-policy"]).toContain("camera=()");
    expect(h["content-security-policy"]).toContain("frame-ancestors 'none'");
    const ro = h["content-security-policy-report-only"];
    expect(ro).toContain("default-src 'self'");
    expect(ro).toContain("connect-src");
    expect(ro).not.toContain("'unsafe-eval'");
  });
}
