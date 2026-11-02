import { describe, it, expect } from "vitest";
import { buildSecurityHeaders } from "./security-headers.mjs";

type Env = { nodeEnv: string; apiUrl?: string };

function headers(env: Env) {
  const list = buildSecurityHeaders(env);
  return Object.fromEntries(list.map((h: { key: string; value: string }) => [h.key, h.value]));
}

const prod = { nodeEnv: "production", apiUrl: "https://api.example.com" };
const dev = { nodeEnv: "development", apiUrl: "http://localhost:8000" };

describe("buildSecurityHeaders", () => {
  it("sets the static hardening headers", () => {
    const h = headers(prod);
    expect(h["X-Frame-Options"]).toBe("DENY");
    expect(h["X-Content-Type-Options"]).toBe("nosniff");
    expect(h["Referrer-Policy"]).toBe("strict-origin-when-cross-origin");
  });

  it("denies powerful features in Permissions-Policy", () => {
    const pp = headers(prod)["Permissions-Policy"];
    for (const f of ["camera", "microphone", "geolocation", "payment", "usb"]) {
      expect(pp).toContain(`${f}=()`);
    }
  });

  it("sends HSTS in production only", () => {
    expect(headers(prod)["Strict-Transport-Security"]).toBe("max-age=31536000; includeSubDomains");
    expect(headers(dev)["Strict-Transport-Security"]).toBeUndefined();
  });

  it("enforces frame-ancestors none in a separate enforced CSP", () => {
    const h = headers(prod);
    expect(h["Content-Security-Policy"]).toContain("frame-ancestors 'none'");
  });

  it("ships the full CSP as report-only", () => {
    const ro = headers(prod)["Content-Security-Policy-Report-Only"]!;
    expect(ro).toContain("default-src 'self'");
    expect(ro).toContain("object-src 'none'");
    expect(ro).toContain("frame-ancestors 'none'");
    expect(ro).toContain("https://apis.google.com");
    expect(ro).toContain("https://docs.google.com");
  });

  it("allows the API origin and its websocket form in connect-src", () => {
    const ro = headers(prod)["Content-Security-Policy-Report-Only"]!;
    const connect = ro.split(";").find((d) => d.trim().startsWith("connect-src"))!;
    expect(connect).toContain("https://api.example.com");
    expect(connect).toContain("wss://api.example.com");
    const devConnect = (headers(dev)["Content-Security-Policy-Report-Only"] ?? "")
      .split(";")
      .find((d) => d.trim().startsWith("connect-src"))!;
    expect(devConnect).toContain("http://localhost:8000");
    expect(devConnect).toContain("ws://localhost:8000");
  });

  it("falls back to localhost:8000 when no API url is set", () => {
    const ro = headers({ nodeEnv: "development" })["Content-Security-Policy-Report-Only"]!;
    expect(ro).toContain("ws://localhost:8000");
  });

  it("allows unsafe-eval only in development", () => {
    expect(headers(prod)["Content-Security-Policy-Report-Only"]).not.toContain("'unsafe-eval'");
    expect(headers(dev)["Content-Security-Policy-Report-Only"]).toContain("'unsafe-eval'");
  });
});
